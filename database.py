import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
import config

logger = logging.getLogger("DuplicateBot.DB")

class Database:
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.media_col = None
        self.stats_col = None
        self.chats_col = None
        self.log_col = None

    async def connect(self):
        """Connect to MongoDB and set up multikey indexes for all factors."""
        try:
            self.client = AsyncIOMotorClient(config.MONGO_URI)
            self.db = self.client[config.DATABASE_NAME]
            self.media_col = self.db["media_hashes"]
            self.stats_col = self.db["bot_stats"]
            self.chats_col = self.db["monitored_chats"]
            self.log_col = self.db["deletion_log"]

            # Ping database to confirm connection
            await self.client.admin.command("ping")
            logger.info("Connected to MongoDB successfully.")

            # Create Multikey Compound Indexes
            await self.media_col.create_index([("chat_id", 1), ("sigs", 1)])
            await self.media_col.create_index([("chat_id", 1), ("created_at", -1)])
            await self.media_col.create_index([("chat_id", 1), ("message_id", 1)])
            await self.chats_col.create_index([("chat_id", 1)], unique=True)
            if config.LOG_DELETIONS:
                await self.log_col.create_index(
                    [("created_at", 1)],
                    expireAfterSeconds=config.DELETION_LOG_DAYS * 86400
                )

            logger.info("MongoDB multikey indexes verified.")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise

    async def register_chat(self, chat_id: int, title: str, chat_type: str):
        """Record chat where bot is running."""
        try:
            await self.chats_col.update_one(
                {"chat_id": chat_id},
                {
                    "$set": {
                        "title": title,
                        "type": chat_type,
                        "last_active": datetime.utcnow()
                    },
                    "$setOnInsert": {
                        "joined_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error registering chat {chat_id}: {e}")

    async def find_duplicate_by_signatures(
        self,
        chat_id: int,
        signatures: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Check if any signature matches an existing document in this chat.
        Matches across all factors (Unique ID, Dimensions, Duration, Thumbnail, Filename, Caption).
        """
        if not signatures:
            return None

        try:
            existing = await self.media_col.find_one({
                "chat_id": chat_id,
                "sigs": {"$in": signatures}
            })
            return existing
        except Exception as e:
            logger.error(f"Error searching duplicate by signatures: {e}")
            return None

    async def save_media_signatures(
        self,
        chat_id: int,
        signatures: List[str],
        media_type: str,
        file_size: int,
        message_id: int
    ):
        """Save media signatures to MongoDB."""
        if not signatures:
            return

        try:
            doc = {
                "chat_id": chat_id,
                "sigs": signatures,
                "media_type": media_type,
                "file_size": file_size,
                "message_id": message_id,
                "created_at": datetime.utcnow()
            }
            await self.media_col.insert_one(doc)
        except Exception as e:
            logger.error(f"Error saving media record for msg {message_id}: {e}")

    async def record_deletion(self, chat_id: int, media_type: str):
        """Increment deletion statistics."""
        await self.record_deletions(chat_id, media_type, 1)

    async def clear_chat_history(self, chat_id: int) -> int:
        """Clear all stored media hashes for a specific chat/channel."""
        try:
            res = await self.media_col.delete_many({"chat_id": chat_id})
            return res.deleted_count
        except Exception as e:
            logger.error(f"Error clearing history for {chat_id}: {e}")
            return 0

    async def delete_record(self, record_id) -> None:
        """Remove one stored record (used when its original message no longer exists)."""
        try:
            await self.media_col.delete_one({"_id": record_id})
        except Exception as e:
            logger.error(f"Error deleting stale record: {e}")

    async def bulk_seed(self, chat_id: int, docs: List[Dict[str, Any]]) -> None:
        """Upsert originals found during /clean so live detection knows them too."""
        if not docs:
            return
        from pymongo import UpdateOne
        now = datetime.utcnow()
        ops = [
            UpdateOne(
                {"chat_id": chat_id, "message_id": d["message_id"]},
                {"$set": {
                    "sigs": d["sigs"],
                    "media_type": d["media_type"],
                    "file_size": d["file_size"],
                }, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
            for d in docs
        ]
        try:
            for i in range(0, len(ops), 500):
                await self.media_col.bulk_write(ops[i:i + 500], ordered=False)
        except Exception as e:
            logger.error(f"Error seeding originals for {chat_id}: {e}")

    async def record_deletions(self, chat_id: int, media_type: str, count: int):
        """Increment stats by `count` in a single round trip."""
        if count <= 0:
            return
        try:
            await self.stats_col.update_one(
                {"_id": "global_stats"},
                {"$inc": {"total_deleted": count, f"type_{media_type}_deleted": count}},
                upsert=True
            )
            await self.chats_col.update_one(
                {"chat_id": chat_id}, {"$inc": {"deleted_here": count}}
            )
        except Exception as e:
            logger.error(f"Error recording deletion stats: {e}")

    async def log_deletions(self, chat_id: int, entries: List[Dict[str, Any]]):
        """Audit trail: which message was deleted as a copy of which original, and why."""
        if not config.LOG_DELETIONS or not entries:
            return
        now = datetime.utcnow()
        try:
            await self.log_col.insert_many(
                [{"chat_id": chat_id, "created_at": now, **e} for e in entries]
            )
        except Exception as e:
            logger.error(f"Error writing deletion log: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        """Fetch total statistics."""
        try:
            stats = await self.stats_col.find_one({"_id": "global_stats"}) or {}
            total_chats = await self.chats_col.count_documents({})
            total_records = await self.media_col.count_documents({})
            return {
                "total_deleted": stats.get("total_deleted", 0),
                "photos_deleted": stats.get("type_photo_deleted", 0),
                "videos_deleted": stats.get("type_video_deleted", 0),
                "audio_deleted": stats.get("type_audio_deleted", 0),
                "documents_deleted": stats.get("type_document_deleted", 0),
                "animations_deleted": stats.get("type_animation_deleted", 0),
                "voice_deleted": stats.get("type_voice_deleted", 0),
                "chats_count": total_chats,
                "total_records": total_records
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"total_deleted": 0, "chats_count": 0, "total_records": 0}

db = Database()

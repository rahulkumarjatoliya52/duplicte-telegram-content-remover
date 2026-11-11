"""
MongoDB layer built for a 512 MB free cluster.

* media  : tiny temporary records {c: chat, m: message id, h: [int hashes], t: time}
           -> TTL index, per-chat cap, global soft limit. History scans never use it.
* chats  : one small doc per chat, scoped by owner_id so users only ever see their own chats.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

import config

logger = logging.getLogger("DuplicateBot.DB")
MB = 1024 * 1024


class Database:
    def __init__(self):
        self.client = None
        self.db = None
        self.media = None
        self.chats = None
        self.ok = False

    # ------------------------------ setup ------------------------------ #
    async def connect(self):
        self.ok = False
        if self.client:
            self.client.close()
        self.client = AsyncIOMotorClient(config.MONGO_URI, serverSelectionTimeoutMS=10000, maxPoolSize=10)
        await self.client.admin.command("ping")
        self.db = self.client[config.DATABASE_NAME]
        self.media = self.db["media"]
        self.chats = self.db["chats"]
        await self._indexes()
        if config.DROP_LEGACY_DATA:
            await self._drop_legacy()
        self.ok = True
        logger.info("✅ Connected to MongoDB")

    async def _indexes(self):
        await self.media.create_index([("c", 1), ("h", 1)], name="chat_hash")
        ttl = max(1, config.RECORD_TTL_DAYS) * 86400
        try:
            await self.media.create_index([("t", 1)], expireAfterSeconds=ttl, name="t_ttl")
        except OperationFailure:
            await self.db.command({"collMod": "media", "index": {"name": "t_ttl", "expireAfterSeconds": ttl}})
        await self.chats.create_index([("chat_id", 1)], unique=True, name="chat_id")
        await self.chats.create_index([("owner_id", 1)], name="owner")

    async def _drop_legacy(self):
        names = await self.db.list_collection_names()
        if "monitored_chats" in names:
            async for d in self.db["monitored_chats"].find({}):
                await self.register_chat(d.get("chat_id"), d.get("title", "Untitled"), d.get("type", "group"), 0)
        for n in ("media_hashes", "bot_stats", "deletion_log", "monitored_chats"):
            if n in names:
                await self.db.drop_collection(n)
                logger.info(f"🧹 Dropped legacy collection: {n}")

    # ------------------------------ chats ------------------------------ #
    async def register_chat(self, chat_id: int, title: str, ctype: str, owner_id: int = 0):
        sets: Dict[str, Any] = {"title": title, "type": ctype}
        on_insert: Dict[str, Any] = {"deleted": 0, "added": datetime.utcnow()}
        if owner_id:
            sets["owner_id"] = owner_id
        else:
            on_insert["owner_id"] = 0
        await self.chats.update_one({"chat_id": chat_id}, {"$set": sets, "$setOnInsert": on_insert}, upsert=True)

    async def claim_chat(self, chat_id: int, user_id: int):
        """First verified admin to use an orphan chat becomes its owner."""
        await self.chats.update_one({"chat_id": chat_id, "owner_id": 0}, {"$set": {"owner_id": user_id}})

    async def get_chat(self, chat_id: int) -> Optional[Dict[str, Any]]:
        return await self.chats.find_one({"chat_id": chat_id})

    async def chats_of_owner(self, owner_id: int, skip: int, limit: int) -> Tuple[List[Dict[str, Any]], int]:
        q = {"owner_id": owner_id}
        total = await self.chats.count_documents(q)
        docs = await self.chats.find(q).sort("added", -1).skip(skip).limit(limit).to_list(limit)
        return docs, total

    async def remove_chat(self, chat_id: int):
        await self.chats.delete_one({"chat_id": chat_id})
        await self.wipe_records(chat_id)

    async def add_deleted(self, chat_id: int, n: int):
        if n > 0:
            await self.chats.update_one({"chat_id": chat_id}, {"$inc": {"deleted": n}})

    async def mark_clean(self, chat_id: int, deleted: int):
        await self.chats.update_one(
            {"chat_id": chat_id}, {"$set": {"last_clean": datetime.utcnow(), "last_clean_deleted": deleted}}
        )

    # ------------------------------ media records ------------------------------ #
    async def find_match(self, chat_id: int, hashes: List[int]) -> Optional[Dict[str, Any]]:
        return await self.media.find_one({"c": chat_id, "h": {"$in": hashes}}, {"m": 1, "h": 1})

    async def save(self, chat_id: int, message_id: int, hashes: List[int]):
        await self.media.insert_one({"c": chat_id, "m": message_id, "h": hashes, "t": datetime.utcnow()})

    async def delete_record(self, record_id):
        await self.media.delete_one({"_id": record_id})

    async def wipe_records(self, chat_id: int) -> int:
        res = await self.media.delete_many({"c": chat_id})
        return res.deleted_count

    async def count_records(self, chat_id: int) -> int:
        return await self.media.count_documents({"c": chat_id})

    async def trim_chat(self, chat_id: int, keep: int):
        total = await self.count_records(chat_id)
        if total <= keep:
            return
        docs = await self.media.find({"c": chat_id}, {"_id": 1}).sort("t", 1).limit(total - keep).to_list(total - keep)
        await self.media.delete_many({"_id": {"$in": [d["_id"] for d in docs]}})

    # ------------------------------ stats / space ------------------------------ #
    async def owner_stats(self, owner_id: int) -> Dict[str, int]:
        cur = self.chats.aggregate([
            {"$match": {"owner_id": owner_id}},
            {"$group": {"_id": None, "chats": {"$sum": 1}, "deleted": {"$sum": "$deleted"}}},
        ])
        rows = await cur.to_list(1)
        return rows[0] if rows else {"chats": 0, "deleted": 0}

    async def global_stats(self) -> Dict[str, Any]:
        cur = self.chats.aggregate([{"$group": {"_id": None, "chats": {"$sum": 1}, "deleted": {"$sum": "$deleted"},
                                                "owners": {"$addToSet": "$owner_id"}}}])
        rows = await cur.to_list(1)
        r = rows[0] if rows else {"chats": 0, "deleted": 0, "owners": []}
        return {
            "chats": r["chats"],
            "deleted": r["deleted"],
            "users": len([o for o in r["owners"] if o]),
            "records": await self.media.estimated_document_count(),
            "size_mb": await self.size_mb(),
        }

    async def size_mb(self) -> float:
        try:
            s = await self.db.command("dbStats")
            return round((s.get("dataSize", 0) + s.get("indexSize", 0)) / MB, 1)
        except Exception:
            return -1.0

    async def enforce_limit(self):
        """Keep the database under DB_SOFT_LIMIT_MB by dropping the oldest temporary records."""
        for _ in range(6):
            size = await self.size_mb()
            if size < 0 or size <= config.DB_SOFT_LIMIT_MB:
                return
            total = await self.media.estimated_document_count()
            n = max(1000, total // 5)
            docs = await self.media.find({}, {"_id": 1}).sort("t", 1).limit(n).to_list(n)
            if not docs:
                return
            await self.media.delete_many({"_id": {"$in": [d["_id"] for d in docs]}})
            logger.warning(f"DB at {size} MB (> {config.DB_SOFT_LIMIT_MB}); purged {len(docs)} oldest records")


db = Database()

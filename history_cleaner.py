import asyncio
import logging
import time
from typing import Dict, Any, List, Optional

from pyrogram import Client
from pyrogram.errors import FloodWait
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

import config
from database import db
from duplicate_detector import extract_pyro_signatures, factor_of

logger = logging.getLogger("DuplicateBot.HistoryCleaner")

# Only one history clean at a time
clean_lock = asyncio.Lock()

pyro_client = Client(
    "duplicate_bot_session",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    no_updates=True,
    workdir="."
)

_pyro_started = False
_pyro_start_lock = asyncio.Lock()


async def ensure_pyro_started():
    global _pyro_started
    async with _pyro_start_lock:
        if not _pyro_started:
            await pyro_client.start()
            _pyro_started = True
            logger.info("HistoryCleaner Pyrogram reader started.")


async def message_exists(chat_id: int, message_id: int) -> Optional[bool]:
    """
    True  -> message still exists and still carries media
    False -> message is gone / has no media
    None  -> could not verify (caller must treat as "do NOT delete")
    """
    try:
        await ensure_pyro_started()
        try:
            msg = await pyro_client.get_messages(chat_id, message_id)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            msg = await pyro_client.get_messages(chat_id, message_id)
        if not msg or getattr(msg, "empty", False):
            return False
        return bool(msg.media)
    except Exception as e:
        logger.warning(f"Could not verify original {message_id} in {chat_id}: {e}")
        return None


async def _fetch(chat_id: int, ids: List[int]):
    for _ in range(3):
        try:
            return await pyro_client.get_messages(chat_id, ids)
        except FloodWait as e:
            logger.warning(f"FloodWait {e.value}s while fetching")
            await asyncio.sleep(e.value)
    return None


async def _delete(bot: Bot, chat_id: int, ids: List[int]) -> int:
    for _ in range(3):
        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=ids)
            return len(ids)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error(f"Batch delete failed ({e}); falling back to single deletes")
            ok = 0
            for mid in ids:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=mid)
                    ok += 1
                except Exception:
                    pass
            return ok
    return 0


async def fast_clean_chat(
    bot: Bot,
    chat_id: int,
    max_id: int,
    min_id: int = 1,
    status_msg=None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Streaming cleaner (oldest -> newest). The OLDEST copy is always the original and is
    never deleted. dry_run=True scans and reports but deletes nothing.
    """
    await ensure_pyro_started()

    total_scanned = total_media = total_deleted = total_flagged = 0
    seen: Dict[str, int] = {}                 # signature -> original message id
    originals: Dict[int, Dict[str, Any]] = {} # original msg id -> record to seed in DB
    type_counts: Dict[str, int] = {}
    factor_counts: Dict[str, int] = {}
    preview: List[str] = []

    batch_size = 100
    last_ui = time.time()
    start = time.time()
    current = min_id

    while current <= max_id:
        end = min(current + batch_size - 1, max_id)
        msgs = await _fetch(chat_id, list(range(current, end + 1)))
        if msgs is None:
            logger.error(f"Skipping batch {current}-{end} (fetch failed, nothing deleted)")
            current += batch_size
            continue

        dups: List[Dict[str, Any]] = []
        for msg in msgs:
            total_scanned += 1
            if not msg or getattr(msg, "empty", False) or not msg.media:
                continue
            sigs, mtype, fsize = extract_pyro_signatures(msg)
            if not sigs:
                continue
            total_media += 1

            hit = next((s for s in sigs if s in seen), None)
            if hit:
                orig_id = seen[hit]
                dups.append({"id": msg.id, "orig": orig_id, "factor": factor_of(hit), "type": mtype})
            else:
                for s in sigs:
                    seen[s] = msg.id
                originals[msg.id] = {"message_id": msg.id, "sigs": sigs,
                                     "media_type": mtype, "file_size": fsize}

        total_flagged += len(dups)
        for d in dups:
            type_counts[d["type"]] = type_counts.get(d["type"], 0) + 1
            factor_counts[d["factor"]] = factor_counts.get(d["factor"], 0) + 1
            if len(preview) < 15:
                preview.append(f"{d['id']} -> copy of {d['orig']} ({d['factor']})")

        if dups and not dry_run:
            ids = [d["id"] for d in dups]
            for i in range(0, len(ids), 100):
                total_deleted += await _delete(bot, chat_id, ids[i:i + 100])
            await db.log_deletions(chat_id, [
                {"message_id": d["id"], "original_id": d["orig"],
                 "factor": d["factor"], "media_type": d["type"], "source": "history_clean"}
                for d in dups
            ])

        if status_msg and time.time() - last_ui > 3.0:
            pct = min(round(end / max_id * 100, 1), 100.0) if max_id else 100.0
            label = "DRY RUN (nothing will be deleted)" if dry_run else "Cleaning"
            try:
                await status_msg.edit_text(
                    f"⚡ <b>{label}...</b>\n\n"
                    f"📊 <b>Progress:</b> <code>{end}/{max_id}</code> ({pct}%)\n"
                    f"📁 <b>Media Analyzed:</b> <code>{total_media}</code>\n"
                    f"🗑️ <b>{'Duplicates Found' if dry_run else 'Deleted'}:</b> "
                    f"<code>{total_flagged if dry_run else total_deleted}</code>"
                )
                last_ui = time.time()
            except Exception:
                pass

        current += batch_size
        await asyncio.sleep(0.7)

    if not dry_run:
        # Per-type stats in one write each, and teach live mode about every original.
        for mtype, cnt in type_counts.items():
            await db.record_deletions(chat_id, mtype, cnt)
        await db.bulk_seed(chat_id, list(originals.values()))

    elapsed = round(time.time() - start, 1)
    logger.info(f"✅ CLEAN {'(dry run) ' if dry_run else ''}done: scanned={total_scanned} "
                f"flagged={total_flagged} deleted={total_deleted} in {elapsed}s")
    return {
        "chat_id": chat_id,
        "dry_run": dry_run,
        "total_scanned": total_scanned,
        "total_media": total_media,
        "duplicates_flagged": total_flagged,
        "duplicates_deleted": total_deleted,
        "originals_kept": total_media - total_flagged,
        "elapsed_seconds": elapsed,
        "type_counts": type_counts,
        "factor_counts": factor_counts,
        "preview": preview,
    }

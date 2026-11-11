"""
History cleaner. Runs entirely in memory: nothing is written to the database while scanning.
The OLDEST copy of every file is the original and is never deleted.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from pyrogram import Client
from pyrogram.errors import FloodWait

import config
import ui
from duplicate_detector import extract_pyro_signatures

logger = logging.getLogger("DuplicateBot.HistoryCleaner")

# in_memory=True: no session file, works on hosts with ephemeral disks
pyro_client = Client(
    "duplicate_bot_reader",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    no_updates=True,
    in_memory=True,
)

_started = False
_start_lock = asyncio.Lock()
_stop_flags: Dict[int, asyncio.Event] = {}


async def ensure_pyro_started():
    global _started
    async with _start_lock:
        if not _started:
            await pyro_client.start()
            _started = True
            logger.info("History reader started")


async def shutdown_pyro():
    global _started
    if _started:
        try:
            await pyro_client.stop()
        except Exception:
            pass
        _started = False


def request_stop(chat_id: int) -> bool:
    ev = _stop_flags.get(chat_id)
    if ev:
        ev.set()
        return True
    return False


async def message_exists(chat_id: int, message_id: int) -> Optional[bool]:
    """True = exists with media, False = gone, None = could not verify (caller must NOT delete)."""
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
        logger.warning(f"Could not verify {message_id} in {chat_id}: {e}")
        return None


async def _fetch(chat_id: int, ids: List[int]):
    for _ in range(3):
        try:
            return await pyro_client.get_messages(chat_id, ids)
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.error(f"Fetch failed for {ids[0]}-{ids[-1]}: {e}")
            return None
    return None


async def _delete(bot: Bot, chat_id: int, ids: List[int]) -> int:
    for _ in range(3):
        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=ids)
            return len(ids)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception:
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
    progress_markup=None,
) -> Dict[str, Any]:
    await ensure_pyro_started()
    stop = _stop_flags[chat_id] = asyncio.Event()

    scanned = media = deleted = flagged = 0
    seen: Dict[int, int] = {}           # fingerprint hash -> original message id
    type_counts: Dict[str, int] = {}
    factor_counts: Dict[str, int] = {}
    preview: List[str] = []
    last_ui = time.time()
    started = time.time()
    current = min_id
    cancelled = False

    try:
        while current <= max_id:
            if stop.is_set():
                cancelled = True
                break
            end = min(current + 99, max_id)
            msgs = await _fetch(chat_id, list(range(current, end + 1)))
            if msgs is None:
                current = end + 1   # fetch failed -> skip batch, delete nothing
                continue

            dups = []
            for msg in msgs:
                scanned += 1
                if not msg or getattr(msg, "empty", False) or not msg.media:
                    continue
                fps, mtype, _size = extract_pyro_signatures(msg)
                if not fps:
                    continue
                media += 1
                hit = next(((h, f) for h, f in fps if h in seen), None)
                if hit:
                    dups.append({"id": msg.id, "orig": seen[hit[0]], "factor": hit[1], "type": mtype})
                else:
                    for h, _f in fps:
                        seen[h] = msg.id

            flagged += len(dups)
            for d in dups:
                type_counts[d["type"]] = type_counts.get(d["type"], 0) + 1
                factor_counts[d["factor"]] = factor_counts.get(d["factor"], 0) + 1
                if len(preview) < 10:
                    preview.append(f"{d['id']} -> {d['orig']}")

            if dups and not dry_run:
                ids = [d["id"] for d in dups]
                for i in range(0, len(ids), 100):
                    deleted += await _delete(bot, chat_id, ids[i:i + 100])

            if status_msg and time.time() - last_ui > 3.0:
                try:
                    await status_msg.edit_text(
                        ui.progress_text(end - min_id + 1, max_id - min_id + 1, media,
                                         flagged if dry_run else deleted, dry_run),
                        reply_markup=progress_markup,
                    )
                    last_ui = time.time()
                except Exception:
                    pass

            current = end + 1
            await asyncio.sleep(0.7)
    finally:
        _stop_flags.pop(chat_id, None)

    elapsed = round(time.time() - started, 1)
    logger.info(f"clean {'(dry) ' if dry_run else ''}chat={chat_id} scanned={scanned} "
                f"flagged={flagged} deleted={deleted} {elapsed}s cancelled={cancelled}")
    return {
        "chat_id": chat_id,
        "dry_run": dry_run,
        "cancelled": cancelled,
        "total_scanned": scanned,
        "total_media": media,
        "duplicates_flagged": flagged,
        "duplicates_deleted": deleted,
        "originals_kept": media - flagged,
        "elapsed_seconds": elapsed,
        "type_counts": type_counts,
        "factor_counts": factor_counts,
        "preview": preview,
    }

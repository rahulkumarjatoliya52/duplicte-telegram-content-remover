import asyncio
import logging
import re
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllPrivateChats

import config
import keepalive
import ui
from database import db
from duplicate_detector import FACTOR_NAMES, extract_multi_factor_signatures
from history_cleaner import fast_clean_chat, message_exists, request_stop, shutdown_pyro

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger("DuplicateBot")

bot = Bot(token=config.BOT_TOKEN or "0:invalid", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

PRIVATE = F.chat.type == ChatType.PRIVATE
GROUPS = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
MEDIA = F.photo | F.video | F.audio | F.document | F.animation | F.voice | F.video_note

BOT_USERNAME = ""
BOT_ID = 0
known_chats: Set[int] = set()
active_jobs: Set[int] = set()
chat_locks: Dict[int, asyncio.Lock] = {}
save_counter: Dict[int, int] = {}
warned_no_perm: Set[int] = set()
_admin_cache: Dict[Tuple[int, int], Tuple[float, bool]] = {}
_tasks: Set[asyncio.Task] = set()
job_sem = asyncio.Semaphore(max(1, config.MAX_PARALLEL_CLEANS))


# =============================================================== utilities

def bg(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


async def safe_delete(chat_id: int, ids: List[int]):
    ids = [i for i in dict.fromkeys(ids) if i]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=chunk)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.delete_messages(chat_id=chat_id, message_ids=chunk)
            except Exception:
                pass
        except Exception:
            for mid in chunk:  # some may be too old / already gone
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=mid)
                except Exception:
                    pass


def vanish(chat_id: int, ids: List[int], ttl: Optional[int] = None):
    """Delete messages after `ttl` seconds (groups/channels stay tidy)."""
    ttl = config.GROUP_REPLY_TTL if ttl is None else ttl
    if ttl <= 0:
        return

    async def _later():
        await asyncio.sleep(ttl)
        await safe_delete(chat_id, ids)

    bg(_later())


async def edit(msg: types.Message, text: str, kb=None):
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def safe_edit(msg: types.Message, text: str, kb=None):
    try:
        await edit(msg, text, kb)
    except Exception as e:
        logger.debug(f"edit failed: {e}")


class Tidy:
    """Private chats: after every Nth action, delete older messages above the newest bot message."""

    def __init__(self):
        self.ids: Dict[int, List[int]] = {}
        self.count: Dict[int, int] = {}

    def track(self, chat_id: int, *ids: int):
        lst = self.ids.setdefault(chat_id, [])
        lst.extend(i for i in ids if i)
        del lst[:-300]

    async def done(self, chat_id: int, keep: Set[int]):
        n = config.TIDY_EVERY_N_ACTIONS
        if n <= 0:
            return
        self.track(chat_id, *keep)
        self.count[chat_id] = self.count.get(chat_id, 0) + 1
        if self.count[chat_id] < n:
            return
        self.count[chat_id] = 0
        tracked = self.ids.get(chat_id, [])
        old = [i for i in dict.fromkeys(tracked) if i not in keep]
        self.ids[chat_id] = [i for i in dict.fromkeys(tracked) if i in keep]
        if old:
            await safe_delete(chat_id, old)


tidy = Tidy()


async def dm_reply(m: types.Message, text: str, kb=None) -> types.Message:
    r = await m.answer(text, reply_markup=kb)
    tidy.track(m.chat.id, m.message_id, r.message_id)
    await tidy.done(m.chat.id, {r.message_id})
    return r


async def grp_reply(m: types.Message, text: str, kb=None, ttl: Optional[int] = None) -> types.Message:
    r = await m.reply(text, reply_markup=kb)
    vanish(m.chat.id, [m.message_id, r.message_id], ttl)
    return r


# =============================================================== auth & registration

async def can_manage(chat_id: int, user_id: int) -> bool:
    """Bot owners, or a real admin/creator of that chat. Nobody else."""
    if user_id in config.OWNER_IDS:
        return True
    key = (chat_id, user_id)
    hit = _admin_cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        ok = member.status in ("creator", "administrator")
    except Exception:
        ok = False
    _admin_cache[key] = (time.time(), ok)
    return ok


async def msg_admin(m: types.Message) -> bool:
    if m.chat.type == ChatType.CHANNEL:
        return True  # only admins can post in a channel
    if m.sender_chat and m.sender_chat.id == m.chat.id:
        return True  # anonymous admin
    return bool(m.from_user) and await can_manage(m.chat.id, m.from_user.id)


async def ensure_registered(chat: types.Chat, owner_id: int = 0):
    if chat.id in known_chats and not owner_id:
        return
    ctype = "channel" if chat.type == ChatType.CHANNEL else "group"
    await db.register_chat(chat.id, chat.title or "Untitled", ctype, owner_id)
    known_chats.add(chat.id)


async def prepare_chat(chat_id: int, user_id: int) -> Optional[dict]:
    doc = await db.get_chat(chat_id)
    if not doc:
        try:
            chat = await bot.get_chat(chat_id)
        except Exception:
            return None
        await ensure_registered(chat)
    await db.claim_chat(chat_id, user_id)
    return await db.get_chat(chat_id)


async def bot_can_delete(chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, BOT_ID)
    except Exception:
        return False
    return member.status == "administrator" and bool(getattr(member, "can_delete_messages", False))


async def notify_owner(chat_id: int, text: str):
    doc = await db.get_chat(chat_id)
    if doc and doc.get("owner_id"):
        try:
            await bot.send_message(doc["owner_id"], text)
        except Exception:
            pass


DRY_WORDS = {"dry", "dryrun", "test", "preview"}


def parse_args(text: Optional[str]) -> Tuple[bool, Optional[int], int]:
    dry, chat_id, limit = False, None, 0
    for tok in (text or "").lower().split():
        if tok in DRY_WORDS:
            dry = True
        elif re.fullmatch(r"-\d{5,}", tok):
            chat_id = int(tok)
        elif tok.isdigit():
            limit = int(tok)
    return dry, chat_id, limit


async def chats_view(user_id: int, page: int):
    docs, total = await db.chats_of_owner(user_id, page * ui.PER_PAGE, ui.PER_PAGE)
    return ui.chats_text(total, page), ui.chats_kb(docs, page, total)


# =============================================================== commands: general

@dp.message(Command("start", "menu"), PRIVATE)
async def cmd_start(m: types.Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    if arg == "help":
        await dm_reply(m, ui.help_text(), ui.back_kb())
    elif arg == "chats":
        text, kb = await chats_view(m.from_user.id, 0)
        await dm_reply(m, text, kb)
    else:
        await dm_reply(m, ui.home_text(m.from_user.first_name), ui.home_kb(BOT_USERNAME))


@dp.message(Command("start"), GROUPS)
async def cmd_start_group(m: types.Message):
    await grp_reply(m, ui.group_help_text())


@dp.message(Command("help"))
async def cmd_help(m: types.Message):
    if m.chat.type == ChatType.PRIVATE:
        await dm_reply(m, ui.help_text(), ui.back_kb())
    else:
        await grp_reply(m, ui.group_help_text())


@dp.message(Command("about"), PRIVATE)
async def cmd_about(m: types.Message):
    await dm_reply(m, ui.about_text(), ui.back_kb())


@dp.message(Command("commands"), PRIVATE)
async def cmd_commands(m: types.Message):
    await dm_reply(m, ui.commands_text(), ui.back_kb())


@dp.message(Command("chats"), PRIVATE)
async def cmd_chats(m: types.Message):
    text, kb = await chats_view(m.from_user.id, 0)
    await dm_reply(m, text, kb)


@dp.message(Command("ping"))
async def cmd_ping(m: types.Message):
    t0 = time.time()
    r = await m.answer("🏓 …")
    ms = int((time.time() - t0) * 1000)
    up = int(time.time() - keepalive.START_TIME)
    h, rem = divmod(up, 3600)
    text = (f"<b>🏓 Pong</b>\n{ui.SEP}\n⚡ Latency: <b>{ms} ms</b>\n"
            f"⏱ Uptime: <b>{h}h {rem // 60}m</b>\n🗄 Database: <b>{'online' if db.ok else 'offline'}</b>")
    await safe_edit(r, text)
    if m.chat.type == ChatType.PRIVATE:
        tidy.track(m.chat.id, m.message_id, r.message_id)
        await tidy.done(m.chat.id, {r.message_id})
    else:
        vanish(m.chat.id, [m.message_id, r.message_id])


@dp.message(Command("stats"), PRIVATE)
async def cmd_stats(m: types.Message):
    mine = await db.owner_stats(m.from_user.id)
    glob = await db.global_stats() if m.from_user.id in config.OWNER_IDS else None
    await dm_reply(m, ui.stats_text(mine, glob), ui.back_kb())


@dp.message(Command("stats"), GROUPS)
async def cmd_stats_group(m: types.Message):
    if not await msg_admin(m):
        await grp_reply(m, "⛔ Admins only.", ttl=15)
        return
    await ensure_registered(m.chat)
    doc = await db.get_chat(m.chat.id) or {}
    await grp_reply(m, ui.group_stats_text(doc, await db.count_records(m.chat.id)))


# =============================================================== commands: cleaning

@dp.message(Command("clean"))
async def cmd_clean(m: types.Message, command: CommandObject):
    dry, cid, limit = parse_args(command.args)

    if m.chat.type == ChatType.PRIVATE:
        if cid is None:
            text, kb = await chats_view(m.from_user.id, 0)
            await dm_reply(m, text, kb)
            return
        if not await can_manage(cid, m.from_user.id) or not await prepare_chat(cid, m.from_user.id):
            await dm_reply(m, "⛔ You must be an admin of that chat, and the bot must be a member of it.")
            return
        status = await dm_reply(m, ui.starting_text(dry))
        launch_clean(cid, status, dry, limit, m.from_user.id, private=True)
        return

    if m.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not await msg_admin(m):
            await grp_reply(m, "⛔ Admins only.", ttl=15)
            return
        uid = m.from_user.id if m.from_user else 0
        await ensure_registered(m.chat)
        if uid:
            await db.claim_chat(m.chat.id, uid)
        status = await m.reply(ui.starting_text(dry))
        vanish(m.chat.id, [m.message_id], 5)
        doc = await db.get_chat(m.chat.id) or {}
        launch_clean(m.chat.id, status, dry, limit, uid or doc.get("owner_id", 0), private=False)


@dp.channel_post(Command("clean"))
async def channel_clean(m: types.Message, command: CommandObject):
    dry, _cid, limit = parse_args(command.args)
    await ensure_registered(m.chat)
    try:
        await m.delete()
    except Exception:
        pass
    status = await bot.send_message(m.chat.id, ui.starting_text(dry))
    doc = await db.get_chat(m.chat.id) or {}
    launch_clean(m.chat.id, status, dry, limit, doc.get("owner_id", 0), private=False)


@dp.message(Command("reset"))
async def cmd_reset(m: types.Message, command: CommandObject):
    if m.chat.type == ChatType.PRIVATE:
        _dry, cid, _l = parse_args(command.args)
        if cid is None:
            await dm_reply(m, "Use <b>My Chats ➜ Reset data</b>, or <code>/reset &lt;chat_id&gt;</code>.", ui.back_kb())
            return
        doc = await prepare_chat(cid, m.from_user.id) if await can_manage(cid, m.from_user.id) else None
        if not doc:
            await dm_reply(m, "⛔ You must be an admin of that chat.")
            return
        await dm_reply(m, ui.reset_text(doc, await db.count_records(cid)), ui.reset_kb(cid))
        return
    if not await msg_admin(m):
        await grp_reply(m, "⛔ Admins only.", ttl=15)
        return
    n = await db.wipe_records(m.chat.id)
    await grp_reply(m, f"✅ Stored data cleared ({n} records). Live detection restarts from new posts.")


@dp.channel_post(Command("reset"))
async def channel_reset(m: types.Message):
    n = await db.wipe_records(m.chat.id)
    try:
        await m.delete()
    except Exception:
        pass
    r = await bot.send_message(m.chat.id, f"✅ Stored data cleared ({n} records).")
    vanish(m.chat.id, [r.message_id], max(config.GROUP_REPLY_TTL, 10))


# =============================================================== commands: group moderation

@dp.message(Command("del", "delete"), GROUPS)
async def cmd_del(m: types.Message):
    if not await msg_admin(m):
        await grp_reply(m, "⛔ Admins only.", ttl=15)
        return
    if not m.reply_to_message:
        await grp_reply(m, "Reply to the message you want to delete with /del.", ttl=15)
        return
    await safe_delete(m.chat.id, [m.reply_to_message.message_id, m.message_id])


@dp.message(Command("purge"), GROUPS)
async def cmd_purge(m: types.Message, command: CommandObject):
    if not await msg_admin(m):
        await grp_reply(m, "⛔ Admins only.", ttl=15)
        return
    arg = (command.args or "").strip()
    if not arg.isdigit() or not 0 < int(arg) <= 200:
        await grp_reply(m, "Usage: <code>/purge 20</code> (1–200)", ttl=15)
        return
    n = int(arg)
    await safe_delete(m.chat.id, list(range(max(1, m.message_id - n), m.message_id + 1)))


@dp.message(Command("check"), GROUPS)
async def cmd_check(m: types.Message):
    if not await msg_admin(m):
        await grp_reply(m, "⛔ Admins only.", ttl=15)
        return
    target = m.reply_to_message
    info = extract_multi_factor_signatures(target) if target else None
    if not info:
        await grp_reply(m, "Reply to a photo, video, audio or file with /check.", ttl=15)
        return
    _t, fps, _s = info
    doc = await db.find_match(m.chat.id, [h for h, _f in fps])
    if doc and doc["m"] != target.message_id:
        factor = next((f for h, f in fps if h in doc.get("h", [])), "u2")
        await grp_reply(m, f"🔎 <b>Duplicate</b> of message <code>{doc['m']}</code>\n"
                           f"Matched by: {FACTOR_NAMES.get(factor, factor)}")
    else:
        await grp_reply(m, "✅ <b>Original</b>, or not recorded yet.")


# =============================================================== clean job

def launch_clean(target: int, status: types.Message, dry: bool, limit: int, requester: int, private: bool):
    bg(run_clean_job(target, status, dry, limit, requester, private))


async def latest_message_id(target: int, status: types.Message) -> Optional[int]:
    if status.chat.id == target:
        return status.message_id
    try:
        probe = await bot.send_message(target, "🧹")
        mid = probe.message_id
        await probe.delete()
        return mid
    except Exception:
        return None


async def run_clean_job(target: int, status: types.Message, dry: bool, limit: int, requester: int, private: bool):
    back = ui.back_kb("chats") if private else None
    if target in active_jobs:
        await safe_edit(status, "⏳ A job is already running for this chat.", ui.progress_kb(target) if private else None)
        return
    active_jobs.add(target)
    try:
        if not dry and not await bot_can_delete(target):
            await safe_edit(status, "⚠️ <b>I can't delete messages here.</b>\nMake me an admin with the "
                                    "<i>Delete Messages</i> permission, then try again.", back)
            return
        if job_sem.locked():
            await safe_edit(status, "⏳ <b>Queued</b>\nOther cleans are running. Yours starts automatically.")
        async with job_sem:
            await safe_edit(status, ui.starting_text(dry), ui.progress_kb(target))
            latest = await latest_message_id(target, status)
            if not latest:
                await safe_edit(status, "❌ I can't access this chat. Make sure I'm an admin there.", back)
                return
            min_id = max(1, latest - limit) if limit else 1
            res = await fast_clean_chat(bot, target, latest, min_id, status, dry, ui.progress_kb(target))

        wiped = 0
        if not dry:
            await db.add_deleted(target, res["duplicates_deleted"])
            await db.mark_clean(target, res["duplicates_deleted"])
            if config.AUTO_WIPE_AFTER_CLEAN:
                wiped = await db.wipe_records(target)
        doc = await db.get_chat(target) or {}
        report = ui.report_text(res, doc.get("title") or str(target), wiped)

        if private:
            await safe_edit(status, report, ui.report_kb(target, dry, limit, res["duplicates_flagged"] > 0))
        elif status.chat.type == ChatType.CHANNEL:
            sent = False
            if requester:
                try:
                    await bot.send_message(requester, report)
                    sent = True
                except Exception:
                    pass
            if sent:
                await safe_delete(status.chat.id, [status.message_id])
            else:
                await safe_edit(status, report)
                vanish(status.chat.id, [status.message_id], config.GROUP_REPLY_TTL * 3)
        else:
            await safe_edit(status, report)
            vanish(status.chat.id, [status.message_id], config.GROUP_REPLY_TTL * 3)
    except Exception as e:
        logger.exception("clean job failed")
        await safe_edit(status, f"❌ Something went wrong: <code>{ui.e(str(e)[:200])}</code>", back)
    finally:
        active_jobs.discard(target)


# =============================================================== callbacks (private panel)

async def show_panel(msg: types.Message, cid: int, uid: int):
    doc = await db.get_chat(cid) or await prepare_chat(cid, uid)
    if not doc:
        await edit(msg, "❌ Chat not found. Is the bot still a member?", ui.back_kb("chats"))
        return
    await edit(msg, ui.panel_text(doc, await db.count_records(cid)), ui.panel_kb(cid))


@dp.callback_query()
async def on_callback(q: types.CallbackQuery):
    msg = q.message
    if not msg or msg.chat.type != ChatType.PRIVATE:
        await q.answer()
        return
    uid = q.from_user.id
    parts = (q.data or "").split(":")
    act = parts[0]
    tidy.track(msg.chat.id, msg.message_id)
    note = None
    try:
        if act == "nav":
            where = parts[1]
            if where == "home":
                await edit(msg, ui.home_text(q.from_user.first_name), ui.home_kb(BOT_USERNAME))
            elif where == "chats":
                text, kb = await chats_view(uid, int(parts[2]) if len(parts) > 2 else 0)
                await edit(msg, text, kb)
            elif where == "stats":
                mine = await db.owner_stats(uid)
                glob = await db.global_stats() if uid in config.OWNER_IDS else None
                await edit(msg, ui.stats_text(mine, glob), ui.back_kb())
            elif where == "help":
                await edit(msg, ui.help_text(), ui.back_kb())
            elif where == "cmds":
                await edit(msg, ui.commands_text(), ui.back_kb())
            elif where == "about":
                await edit(msg, ui.about_text(), ui.back_kb())

        elif act in ("chat", "dry", "ask", "run", "rst", "rstok", "stop"):
            cid = int(parts[1])
            if not await can_manage(cid, uid):
                await q.answer("⛔ You are not an admin of this chat.", show_alert=True)
                return
            limit = int(parts[2]) if len(parts) > 2 else 0
            if act == "chat":
                await show_panel(msg, cid, uid)
            elif act == "dry":
                launch_clean(cid, msg, True, limit, uid, private=True)
            elif act == "run":
                launch_clean(cid, msg, False, limit, uid, private=True)
            elif act == "ask":
                doc = await db.get_chat(cid) or await prepare_chat(cid, uid) or {}
                await edit(msg, ui.confirm_text(doc, limit), ui.confirm_kb(cid, limit))
            elif act == "rst":
                doc = await db.get_chat(cid) or {}
                await edit(msg, ui.reset_text(doc, await db.count_records(cid)), ui.reset_kb(cid))
            elif act == "rstok":
                n = await db.wipe_records(cid)
                note = f"Cleared {n} records"
                await show_panel(msg, cid, uid)
            elif act == "stop":
                note = "Stopping…" if request_stop(cid) else "Nothing is running."
    except Exception:
        logger.exception("callback failed")
        note = "Something went wrong. Try again."
    await q.answer(note)
    await tidy.done(msg.chat.id, {msg.message_id})


# =============================================================== live detection

async def process_media_message(message: types.Message):
    """
    Delete a new file ONLY if a strong fingerprint matches AND the stored original still exists.
    Any doubt (DB error, cannot verify) -> keep the file.
    """
    info = extract_multi_factor_signatures(message)
    if not info:
        return
    mtype, fps, _size = info
    chat = message.chat
    cid = chat.id
    hashes = [h for h, _f in fps]
    try:
        await ensure_registered(chat)
        lock = chat_locks.setdefault(cid, asyncio.Lock())
        async with lock:  # find+save must be atomic, or two simultaneous copies both look "original"
            doc = await db.find_match(cid, hashes)
            if doc and doc["m"] == message.message_id:
                return
            if doc and config.VERIFY_ORIGINAL:
                exists = await message_exists(cid, doc["m"])
                if exists is None:
                    return  # cannot verify -> keep
                if exists is False:
                    await db.delete_record(doc["_id"])
                    doc = None
            if not doc:
                await db.save(cid, message.message_id, hashes)
                save_counter[cid] = save_counter.get(cid, 0) + 1
                if save_counter[cid] % 300 == 0:
                    await db.trim_chat(cid, config.MAX_RECORDS_PER_CHAT)
                return
    except Exception as e:
        logger.error(f"live check failed (message kept): {e}")
        return

    factor = next((f for h, f in fps if h in doc.get("h", [])), "u2")
    logger.info(f"duplicate chat={cid} msg={message.message_id} of={doc['m']} via={factor} type={mtype}")
    if config.DELAY > 0:
        await asyncio.sleep(config.DELAY)

    for _ in range(2):
        try:
            await bot.delete_message(chat_id=cid, message_id=message.message_id)
            await db.add_deleted(cid, 1)
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            if cid not in warned_no_perm:
                warned_no_perm.add(cid)
                await notify_owner(cid, f"⚠️ I can't delete messages in <b>{ui.e(chat.title)}</b>.\n"
                                        f"Please give me the <i>Delete Messages</i> permission.")
            logger.warning(f"cannot delete {message.message_id} in {cid}: {e}")
            return
        except Exception as e:
            logger.error(f"delete error: {e}")
            return


@dp.channel_post(MEDIA)
async def on_channel_media(m: types.Message):
    await process_media_message(m)


@dp.message(GROUPS, MEDIA)
async def on_group_media(m: types.Message):
    await process_media_message(m)


# =============================================================== membership events

@dp.my_chat_member()
async def on_bot_membership(ev: types.ChatMemberUpdated):
    chat = ev.chat
    if chat.type == ChatType.PRIVATE:
        return
    status = ev.new_chat_member.status
    if status in ("left", "kicked"):
        await db.remove_chat(chat.id)  # frees every stored byte of that chat
        known_chats.discard(chat.id)
        warned_no_perm.discard(chat.id)
        return
    if status not in ("administrator", "member"):
        return
    owner = ev.from_user.id if ev.from_user and not ev.from_user.is_bot else 0
    await ensure_registered(chat, owner)
    can_del = status == "administrator" and bool(getattr(ev.new_chat_member, "can_delete_messages", False))
    if owner:
        text = f"<b>✅ Added to {ui.e(chat.title)}</b>\n{ui.SEP}\nOpen your chat list to manage it."
        if not can_del:
            text += "\n\n⚠️ <b>Action needed:</b> make me an admin with the <i>Delete Messages</i> permission."
        try:
            await bot.send_message(owner, text, reply_markup=ui.open_panel_kb(BOT_USERNAME, chat.id))
        except Exception:
            pass
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            r = await bot.send_message(chat.id, ui.group_help_text())
            vanish(chat.id, [r.message_id], max(config.GROUP_REPLY_TTL * 2, 0))
        except Exception:
            pass


@dp.errors()
async def on_error(event: types.ErrorEvent):
    logger.error(f"Unhandled error: {event.exception!r}", exc_info=event.exception)
    return True


# =============================================================== startup / supervisor

async def set_commands():
    private = [
        BotCommand(command="menu", description="Main menu"),
        BotCommand(command="chats", description="My channels & groups"),
        BotCommand(command="clean", description="Clean a chat"),
        BotCommand(command="reset", description="Wipe stored data of a chat"),
        BotCommand(command="stats", description="Statistics"),
        BotCommand(command="help", description="How it works"),
        BotCommand(command="commands", description="All commands"),
        BotCommand(command="about", description="About the bot"),
        BotCommand(command="ping", description="Bot status"),
    ]
    admins = [
        BotCommand(command="clean", description="Clean history (add 'dry' to preview)"),
        BotCommand(command="check", description="Reply to media: duplicate or original?"),
        BotCommand(command="reset", description="Wipe stored data"),
        BotCommand(command="stats", description="Chat statistics"),
        BotCommand(command="del", description="Reply to delete a message"),
        BotCommand(command="purge", description="Delete last N messages"),
        BotCommand(command="help", description="Help"),
        BotCommand(command="ping", description="Bot status"),
    ]
    await bot.set_my_commands(private, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(admins, scope=BotCommandScopeAllChatAdministrators())


async def maintenance_loop():
    while True:
        await asyncio.sleep(1800)
        try:
            if db.ok:
                await db.enforce_limit()
            cutoff = time.time() - 300
            for k in [k for k, v in _admin_cache.items() if v[0] < cutoff]:
                _admin_cache.pop(k, None)
        except Exception as e:
            logger.warning(f"maintenance error: {e}")


async def main():
    global BOT_USERNAME, BOT_ID
    missing = [n for n, v in (("BOT_TOKEN", config.BOT_TOKEN), ("API_ID", config.API_ID),
                              ("API_HASH", config.API_HASH), ("MONGO_URI", config.MONGO_URI)) if not v]
    if missing:
        logger.error(f"Missing configuration: {', '.join(missing)}")
        sys.exit(1)

    keepalive.set_db_check(lambda: db.ok)
    runner = await keepalive.start_web_server()  # open the port first so the host marks us healthy
    bg(keepalive.self_ping_loop())
    bg(maintenance_loop())

    backoff = 2
    try:
        while True:
            try:
                if not db.ok:
                    await db.connect()
                me = await bot.get_me()
                BOT_USERNAME, BOT_ID = me.username, me.id
                await set_commands()
                logger.info(f"🚀 Running as @{BOT_USERNAME}")
                backoff = 2
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
                break  # normal shutdown (SIGTERM / Ctrl+C)
            except (KeyboardInterrupt, asyncio.CancelledError):
                break
            except Exception as e:
                db.ok = False
                logger.error(f"Crashed: {e!r}. Restarting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
    finally:
        await runner.cleanup()
        await shutdown_pyro()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

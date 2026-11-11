import asyncio
import logging
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict

# Windows console UTF-8 fix
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter
)
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ChatMemberUpdated
)

import config
from database import db
from duplicate_detector import extract_multi_factor_signatures, factor_of, FACTOR_NAMES
from history_cleaner import fast_clean_chat, clean_lock, message_exists

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("DuplicateBot")

START_TIME = time.time()
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()


# ----------------- AUTH & HELPERS ----------------- #

chat_locks: Dict[int, asyncio.Lock] = {}

async def is_authorized(message: types.Message) -> bool:
    """Owners (AUTH_USERS) anywhere; group admins inside their own group. Nobody else."""
    user = message.from_user
    if user and user.id in config.AUTH_USERS:
        return True
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if message.sender_chat and message.sender_chat.id == message.chat.id:
            return True  # anonymous admin
        if user:
            try:
                member = await bot.get_chat_member(message.chat.id, user.id)
                return member.status in ("creator", "administrator")
            except Exception:
                return False
    return False

async def deny(message: types.Message):
    await message.reply("⛔ Only the bot owner or a group admin can use this command.")

def parse_clean_args(args: List[str]):
    """'/clean 500 dry' -> ([500], True). Returns (numbers, dry_run)."""
    dry = any(a.lower() in ("dry", "dryrun", "dry-run", "test") for a in args[1:])
    nums = []
    for a in args[1:]:
        try:
            nums.append(int(a))
        except ValueError:
            pass
    return nums, dry

# ----------------- UI BUILDERS ----------------- #

def get_start_markup(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ Add to Channel",
                url=f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages+delete_messages"
            ),
            InlineKeyboardButton(
                text="➕ Add to Group",
                url=f"https://t.me/{bot_username}?startgroup=true&admin=delete_messages"
            )
        ],
        [
            InlineKeyboardButton(text="⚡ Clean Channel History", callback_data="clean_menu"),
            InlineKeyboardButton(text="📖 Help & Setup", callback_data="help_menu")
        ],
        [
            InlineKeyboardButton(text="📊 Live Statistics", callback_data="stats_menu"),
            InlineKeyboardButton(text="📜 Commands", callback_data="cmds_menu")
        ]
    ])

def get_back_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Back to Main Menu", callback_data="start_menu")]
    ])

def get_start_text(first_name: str) -> str:
    return (
        f"👋 <b>Hello {first_name}!</b>\n\n"
        f"I am the <b>Duplicate Media Remover Bot (Safe Multi-Factor Edition)</b>.\n\n"
        f"🛡️ <b>What I do:</b>\n"
        f"I instantly delete duplicate photos, videos, audio and files posted in your Channel or Group!\n\n"
        f"🔍 <b>Safe Detection (originals are never deleted by mistake):</b>\n"
        f"• <b>Telegram Unique ID</b> (same file: copy / forward)\n"
        f"• <b>Exact Size + Resolution + Duration</b> (all must match together)\n"
        f"• The oldest copy is always kept as the original\n"
        f"• Before deleting, I verify the original still exists\n\n"
        f"⚡ <b>Fast History Cleaner:</b> Send <code>/clean</code> in your channel to remove thousands of old duplicates in one go!"
    )

def get_help_text() -> str:
    return (
        f"📖 <b>Help & Setup Guide</b>\n\n"
        f"<b>1. How to use it in a channel?</b>\n"
        f"• Channel settings ➔ Administrators ➔ Add Admin ➔ <code>@DuplicateFilesRemover_bot</code>.\n"
        f"• Enable the <b>'Delete Messages'</b> permission and save.\n"
        f"• New duplicates will be deleted automatically!\n\n"
        f"<b>2. How to delete thousands of old duplicates at once?</b>\n"
        f"• Send <code>/clean</code> in the channel (or <code>/clean &lt;channel_id&gt;</code> in DM).\n"
        f"• The bot matches strict factors (Unique ID, or Size+Resolution+Duration) and deletes only true duplicates!\n"
        f"• To clean only the last N messages: <code>/clean 500</code>\n\n"
        f"<b>3. How are duplicates detected?</b>\n"
        f"• 🆔 Telegram Unique ID (same file)\n"
        f"• ⚖️ Exact Size + 📐 Resolution + ⏱️ Duration (all together)\n"
        f"• Weak matches (duration or caption alone) never delete anything\n\n"
        f"<b>4. Want to test first?</b>\n"
        f"• <code>/clean dry</code> - report only, nothing is deleted"
    )

def get_about_text() -> str:
    return (
        f"ℹ️ <b>About This Bot</b>\n\n"
        f"🤖 <b>Bot Name:</b> Duplicate Files Remover\n"
        f"👤 <b>Username:</b> @DuplicateFilesRemover_bot\n"
        f"⚡ <b>Engine:</b> Python 3.12 + Aiogram 3.x + Multi-Factor Engine\n"
        f"🗄️ <b>Database:</b> MongoDB Atlas (Multikey Compound Indexes)\n"
        f"🔍 <b>Factors:</b> Unique ID | Exact Size + Resolution + Duration\n"
        f"🟢 <b>Status:</b> 24/7 Online & Active"
    )

def get_cmds_text() -> str:
    return (
        f"📜 <b>Available Commands</b>\n\n"
        f"<b>⚡ Multi-Factor History Cleaning:</b>\n"
        f"• <code>/clean</code> - Scan the full channel/group history and delete duplicates in real time\n"
        f"• <code>/clean dry</code> - Test run: report only, nothing is deleted\n"
        f"• <code>/clean &lt;count&gt;</code> - Example: <code>/clean 500</code> (clean only the last 500 messages)\n"
        f"• <code>/clean &lt;channel_id&gt;</code> - Clean a specific channel's history from DM (e.g. <code>/clean -1004466397591</code>)\n\n"
        f"<b>Admin / Manual Delete Commands:</b>\n"
        f"• <code>/del</code> or <code>/delete</code> - Reply to any message to delete it\n"
        f"• <code>/purge &lt;count&gt;</code> - Delete the last N messages at once (e.g. <code>/purge 20</code>)\n"
        f"• <code>/check</code> - Reply to a media message to see if it is a duplicate or an original\n"
        f"• <code>/clean_db</code> - Reset this chat's stored hashes in the database\n\n"
        f"<b>General Commands:</b>\n"
        f"• <code>/start</code> - Open the main menu\n"
        f"• <code>/help</code> - Guide and setup info\n"
        f"• <code>/about</code> - Bot details\n"
        f"• <code>/stats</code> - Total deleted media count\n"
        f"• <code>/ping</code> - Speed test latency"
    )

# ----------------- COMMANDS ----------------- #

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Handle /start command."""
    me = await bot.get_me()
    first_name = message.from_user.first_name if message.from_user else "User"

    if message.chat.type == ChatType.PRIVATE:
        await message.reply(
            get_start_text(first_name),
            reply_markup=get_start_markup(me.username),
            disable_web_page_preview=True
        )
    else:
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Open in Private Chat", url=f"https://t.me/{me.username}?start=help")]
        ])
        await message.reply(
            f"👋 Hello {first_name}!\n"
            f"I am active in this group as the <b>Duplicate Media Remover (Multi-Factor)</b>.\n"
            f"Send <code>/clean</code> to remove duplicates from the full history.",
            reply_markup=buttons
        )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Handle /help command."""
    me = await bot.get_me()
    if message.chat.type == ChatType.PRIVATE:
        await message.reply(get_help_text(), reply_markup=get_back_markup())
    else:
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Read Help in DM", url=f"https://t.me/{me.username}?start=help")]
        ])
        await message.reply("📖 Click to read the setup and help guide:", reply_markup=buttons)

@dp.message(Command("about"))
async def cmd_about(message: types.Message):
    """Handle /about command."""
    await message.reply(get_about_text(), reply_markup=get_back_markup())

@dp.message(Command("cmds", "commands"))
async def cmd_commands(message: types.Message):
    """Handle /cmds command."""
    await message.reply(get_cmds_text(), reply_markup=get_back_markup())

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Handle /stats command."""
    stats = await db.get_stats()
    uptime_sec = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    text = (
        f"📊 <b>Duplicate Remover Statistics</b>\n\n"
        f"🗑️ <b>Total Duplicates Deleted:</b> <code>{stats['total_deleted']}</code>\n"
        f"📸 Photos Deleted: <code>{stats['photos_deleted']}</code>\n"
        f"🎥 Videos Deleted: <code>{stats['videos_deleted']}</code>\n"
        f"🎵 Audio Deleted: <code>{stats['audio_deleted']}</code>\n"
        f"📁 Documents Deleted: <code>{stats['documents_deleted']}</code>\n"
        f"🎞️ Animations/GIFs: <code>{stats['animations_deleted']}</code>\n"
        f"🎙️ Voice Notes: <code>{stats['voice_deleted']}</code>\n\n"
        f"📢 Monitored Chats: <code>{stats['chats_count']}</code>\n"
        f"🗃️ Media Hashes Stored: <code>{stats['total_records']}</code>\n"
        f"⏱️ Uptime: <code>{uptime_str}</code>"
    )
    await message.reply(text, reply_markup=get_back_markup())

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    """Handle /ping command."""
    t1 = time.time()
    msg = await message.reply("🏓 Pinging...")
    t2 = time.time()
    ping_ms = round((t2 - t1) * 1000)
    await msg.edit_text(f"🏓 <b>Pong!</b> <code>{ping_ms} ms</code>\n🤖 Bot is running smoothly.")

# ----------------- /CLEAN FAST STREAMING SCANNER ----------------- #

async def execute_clean_task(target_chat_id: int, reply_to_msg: Optional[types.Message], count_limit: Optional[int] = None, dry_run: bool = False):
    """Helper to run multi-factor streaming cleaner and report results."""
    if clean_lock.locked():
        if reply_to_msg:
            await reply_to_msg.reply("⏳ <b>A cleaning task is already running!</b> Please wait until it finishes.")
        return

    status_msg = None
    if reply_to_msg:
        status_msg = await reply_to_msg.reply("⚡ <b>Cleaning started:</b> matching Unique ID or Size + Resolution + Duration, deleting in real time...")

    async with clean_lock:
        try:
            probe = await bot.send_message(target_chat_id, "🧹")
            latest_id = probe.message_id
            await probe.delete()

            if count_limit and count_limit > 0:
                min_id = max(1, latest_id - count_limit)
            else:
                min_id = 1
            max_id = latest_id

            logger.info(f"Target {target_chat_id} range: {min_id} to {max_id}")

            res = await fast_clean_chat(
                bot=bot,
                chat_id=target_chat_id,
                max_id=max_id,
                min_id=min_id,
                status_msg=status_msg,
                dry_run=dry_run
            )

            counts = res.get("type_counts", {})
            breakdown_lines = []
            if counts.get("video"):
                breakdown_lines.append(f"• 🎥 Videos Deleted: <code>{counts['video']}</code>")
            if counts.get("photo"):
                breakdown_lines.append(f"• 📸 Photos Deleted: <code>{counts['photo']}</code>")
            if counts.get("audio"):
                breakdown_lines.append(f"• 🎵 Audios Deleted: <code>{counts['audio']}</code>")
            if counts.get("document"):
                breakdown_lines.append(f"• 📁 Documents Deleted: <code>{counts['document']}</code>")

            factors = res.get("factor_counts", {})
            factor_lines = [f"• {FACTOR_NAMES.get(k, k)}: <code>{v}</code>" for k, v in factors.items()]
            breakdown_text = "\n".join(breakdown_lines) if breakdown_lines else "• No duplicates found."
            factor_text = "\n".join(factor_lines) if factor_lines else "• -"

            if res["dry_run"]:
                preview = "\n".join(f"<code>{p}</code>" for p in res["preview"]) or "-"
                report = (
                    f"🧪 <b>DRY RUN complete (nothing was deleted)</b>\n\n"
                    f"• 📨 Messages Scanned: <code>{res['total_scanned']}</code>\n"
                    f"• 📁 Media Analyzed: <code>{res['total_media']}</code>\n"
                    f"• 🗑️ <b>Would be deleted:</b> <code>{res['duplicates_flagged']}</code>\n"
                    f"• 💾 Originals Safe: <code>{res['originals_kept']}</code>\n"
                    f"• ⏱️ Time: <code>{res['elapsed_seconds']}s</code>\n\n"
                    f"<b>Matched Factors:</b>\n{factor_text}\n\n"
                    f"<b>Sample (dup id -> original id):</b>\n{preview}\n\n"
                    f"If this looks right, run <code>/clean</code>."
                )
            else:
                report = (
                    f"✅ <b>Clean Complete!</b> 🎉\n\n"
                    f"• 📨 Messages Scanned: <code>{res['total_scanned']}</code>\n"
                    f"• 📁 Media Analyzed: <code>{res['total_media']}</code>\n"
                    f"• 🗑️ <b>Duplicates Deleted:</b> <code>{res['duplicates_deleted']}</code>\n"
                    f"• 💾 Originals Preserved: <code>{res['originals_kept']}</code>\n"
                    f"• ⏱️ Time Taken: <code>{res['elapsed_seconds']}s</code>\n\n"
                    f"<b>Media Types:</b>\n{breakdown_text}\n\n"
                    f"<b>Matched Factors:</b>\n{factor_text}"
                )

            if status_msg:
                await status_msg.edit_text(report)
                if reply_to_msg and reply_to_msg.chat.type == ChatType.CHANNEL:
                    await asyncio.sleep(15)
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
            return report

        except Exception as e:
            logger.error(f"Error executing clean task: {e}")
            err_msg = f"❌ Clean error: {e}"
            if status_msg:
                await status_msg.edit_text(err_msg)
            return err_msg

@dp.message(Command("clean"))
async def cmd_clean_group_or_dm(message: types.Message):
    """Handle /clean in Group or Private DM. Add 'dry' for a test run."""
    if not await is_authorized(message):
        await deny(message)
        return

    args = message.text.split()
    nums, dry = parse_clean_args(args)

    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await execute_clean_task(
            target_chat_id=message.chat.id,
            reply_to_msg=message,
            count_limit=nums[0] if nums else None,
            dry_run=dry
        )

    elif message.chat.type == ChatType.PRIVATE:
        if nums:
            target_id = nums[0]
            count_limit = nums[1] if len(nums) > 1 else None
            await message.reply(f"{'🧪 Dry run' if dry else '⚡ Clean'}: <code>{target_id}</code>...")
            await execute_clean_task(
                target_chat_id=target_id,
                reply_to_msg=message,
                count_limit=count_limit,
                dry_run=dry
            )
        else:
            buttons = []
            async for ch in db.chats_col.find({}).limit(10):
                buttons.append([InlineKeyboardButton(
                    text=f"🧪 Dry run '{ch.get('title', 'Unknown')}'",
                    callback_data=f"do_dry:{ch.get('chat_id')}"
                ), InlineKeyboardButton(
                    text="⚡ Clean",
                    callback_data=f"do_clean:{ch.get('chat_id')}"
                )])
            if buttons:
                buttons.append([InlineKeyboardButton(text="« Back", callback_data="start_menu")])
                await message.reply(
                    "⚡ Which channel/group do you want to clean? A <b>dry run</b> first is recommended:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
                )
            else:
                await message.reply(
                    "⚠️ No chat is registered yet.\n"
                    "Direct: <code>/clean -1004466397591 dry</code>"
                )

# ----------------- MANUAL DELETION COMMANDS ----------------- #

@dp.message(Command("del", "delete"))
async def cmd_delete(message: types.Message):
    """Delete the replied message."""
    if not await is_authorized(message):
        await deny(message)
        return
    if not message.reply_to_message:
        await message.reply("⚠️ <b>How to use:</b> <b>Reply</b> to the message you want to delete with <code>/del</code>.")
        return

    target_msg_id = message.reply_to_message.message_id
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=target_msg_id)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        logger.info(f"🗑️ [MANUAL DELETE] Deleted message {target_msg_id} in {message.chat.id}")
    except TelegramBadRequest as e:
        await message.reply(f"❌ Could not delete: {e.message}")
    except Exception as e:
        logger.error(f"Error in /del: {e}")

@dp.message(Command("purge"))
async def cmd_purge(message: types.Message):
    """Purge last N messages in chat. E.g. /purge 10"""
    if not await is_authorized(message):
        await deny(message)
        return
    args = message.text.split()
    count = 10
    if len(args) > 1 and args[1].isdigit():
        count = min(int(args[1]), 100)

    chat_id = message.chat.id
    current_msg_id = message.message_id
    msg_ids = list(range(current_msg_id - count, current_msg_id + 1))

    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=msg_ids)
        notice = await message.answer(f"🧹 Successfully purged {len(msg_ids)} messages.")
        await asyncio.sleep(4)
        try:
            await notice.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error in /purge: {e}")
        await message.reply(f"❌ Purge error: {e}")

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    """Check if the replied media is recognized as duplicate."""
    if not await is_authorized(message):
        await deny(message)
        return
    if not message.reply_to_message:
        await message.reply("⚠️ <b>Reply</b> to a photo, video or audio with <code>/check</code>.")
        return

    media_info = extract_multi_factor_signatures(message.reply_to_message)
    if not media_info:
        await message.reply("⚠️ No photo, video or audio found in this message.")
        return

    media_type, signatures, file_size = media_info
    dup = await db.find_duplicate_by_signatures(message.chat.id, signatures)

    if dup:
        orig_id = dup.get("message_id", "Unknown")
        created_at = dup.get("created_at", "Unknown")
        await message.reply(
            f"🔍 <b>DUPLICATE DETECTED!</b>\n\n"
            f"• <b>Type:</b> <code>{media_type}</code>\n"
            f"• <b>Original Msg ID:</b> <code>{orig_id}</code>\n"
            f"• <b>Saved At:</b> <code>{created_at}</code>\n"
            f"• <b>Total Factors Checked:</b> <code>{len(signatures)}</code>"
        )
    else:
        await message.reply(
            f"✅ <b>ORIGINAL MEDIA</b>\n\n"
            f"• <b>Type:</b> <code>{media_type}</code>\n"
            f"• <b>Signatures Extracted:</b> <code>{len(signatures)}</code>\n"
            f"• This file was not yet recorded in the database."
        )

@dp.message(Command("clean_db", "reset_db"))
async def cmd_clean_db(message: types.Message):
    """Reset stored media hashes for current chat."""
    if not await is_authorized(message):
        await deny(message)
        return
    chat_id = message.chat.id
    del_count = await db.clear_chat_history(chat_id)
    await message.reply(f"🧹 Reset all <b>{del_count}</b> stored media records for this chat/channel.")

# ----------------- CALLBACK BUTTONS ----------------- #

@dp.callback_query()
async def on_callback(query: CallbackQuery):
    """Handle inline button clicks."""
    data = query.data
    first_name = query.from_user.first_name if query.from_user else "User"
    me = await bot.get_me()

    try:
        if data == "clean_menu" or data.startswith(("do_clean:", "do_dry:")):
            if not query.from_user or query.from_user.id not in config.AUTH_USERS:
                await query.answer("⛔ Bot owner only.", show_alert=True)
                return
        if data == "start_menu":
            await query.message.edit_text(
                get_start_text(first_name),
                reply_markup=get_start_markup(me.username),
                disable_web_page_preview=True
            )
        elif data == "help_menu":
            await query.message.edit_text(get_help_text(), reply_markup=get_back_markup())
        elif data == "about_menu":
            await query.message.edit_text(get_about_text(), reply_markup=get_back_markup())
        elif data == "cmds_menu":
            await query.message.edit_text(get_cmds_text(), reply_markup=get_back_markup())
        elif data == "stats_menu":
            stats = await db.get_stats()
            uptime_sec = int(time.time() - START_TIME)
            hours, remainder = divmod(uptime_sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours}h {minutes}m {seconds}s"
            stats_text = (
                f"📊 <b>Duplicate Remover Statistics</b>\n\n"
                f"🗑️ <b>Total Duplicates Deleted:</b> <code>{stats['total_deleted']}</code>\n"
                f"📸 Photos Deleted: <code>{stats['photos_deleted']}</code>\n"
                f"🎥 Videos Deleted: <code>{stats['videos_deleted']}</code>\n"
                f"🎵 Audio Deleted: <code>{stats['audio_deleted']}</code>\n"
                f"📁 Documents Deleted: <code>{stats['documents_deleted']}</code>\n"
                f"🎞️ Animations/GIFs: <code>{stats['animations_deleted']}</code>\n"
                f"🎙️ Voice Notes: <code>{stats['voice_deleted']}</code>\n\n"
                f"📢 Monitored Chats: <code>{stats['chats_count']}</code>\n"
                f"🗃️ Media Hashes Stored: <code>{stats['total_records']}</code>\n"
                f"⏱️ Uptime: <code>{uptime_str}</code>"
            )
            await query.message.edit_text(stats_text, reply_markup=get_back_markup())

        elif data == "clean_menu":
            cursor = db.chats_col.find({}).limit(10)
            buttons = []
            async for ch in cursor:
                title = ch.get("title", "Unknown")
                cid = ch.get("chat_id")
                buttons.append([
                    InlineKeyboardButton(
                        text=f"⚡ Clean '{title}'",
                        callback_data=f"do_clean:{cid}"
                    )
                ])
            buttons.append([InlineKeyboardButton(text="« Back", callback_data="start_menu")])
            markup = InlineKeyboardMarkup(inline_keyboard=buttons)
            await query.message.edit_text(
                "⚡ <b>Multi-Factor History Cleaner:</b> Choose which Channel or Group history to clean:",
                reply_markup=markup
            )

        elif data.startswith(("do_clean:", "do_dry:")):
            dry = data.startswith("do_dry:")
            cid = int(data.split(":")[1])
            await query.message.edit_text(f"{'🧪 Dry run' if dry else '⚡ Clean'} started: chat <code>{cid}</code>...")
            await execute_clean_task(
                target_chat_id=cid,
                reply_to_msg=query.message,
                count_limit=None,
                dry_run=dry
            )

        await query.answer()
    except Exception as e:
        logger.debug(f"Callback error: {e}")
        await query.answer()

# ----------------- WELCOME GREETINGS ----------------- #

@dp.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    """Trigger when bot is added or promoted in Group or Channel."""
    chat = event.chat
    new_status = event.new_chat_member.status

    if new_status in ("administrator", "member"):
        chat_title = chat.title or "Chat"
        logger.info(f"🎉 Bot added to {chat.type} '{chat_title}' ({chat.id}) with status '{new_status}'")
        asyncio.create_task(db.register_chat(chat.id, chat_title, chat.type))

        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            me = await bot.get_me()
            welcome_text = (
                f"👋 <b>Hello everyone!</b>\n\n"
                f"Thanks for adding me to <b>{chat_title}</b>! 🎉\n\n"
                f"🛡️ I am the <b>Duplicate Media Remover Bot (Multi-Factor)</b>. I will automatically delete all duplicate "
                f"<b>Photos, Videos, Audio and Files</b> posted in this group.\n\n"
                f"⚡ <b>Tip:</b> send <code>/clean</code> in the group to remove duplicates from old history!\n\n"
                f"Please make me a group <b>Admin</b> and grant the <b>'Delete Messages'</b> permission! 🚀"
            )
            buttons = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📖 Help & Setup", url=f"https://t.me/{me.username}?start=help")]
            ])
            try:
                await bot.send_message(chat.id, welcome_text, reply_markup=buttons)
            except Exception as e:
                logger.warning(f"Could not send welcome message in {chat.id}: {e}")

@dp.message(F.new_chat_members)
async def on_new_members(message: types.Message):
    """Trigger when users or bot join group."""
    me = await bot.get_me()
    if any(m.id == me.id for m in message.new_chat_members):
        chat_title = message.chat.title or "this group"
        welcome_text = (
            f"👋 <b>Hello!</b>\n\n"
            f"Thanks for adding me to <b>{chat_title}</b>! 🎉\n\n"
            f"I am active here to delete duplicate photos, videos and audio.\n"
            f"Send the <code>/clean</code> command to clean the full old history!"
        )
        try:
            await message.reply(welcome_text)
        except Exception:
            pass

# ----------------- SHARED LIVE DUPLICATE LOGIC ----------------- #

async def process_media_message(message: types.Message, chat_title: str):
    """
    Safe live check for channels and groups.
    A new file is deleted ONLY if:
      1. a strong fingerprint matches (Unique ID, or Size+Resolution+Duration), AND
      2. the stored original message still exists (verified), AND
      3. verification itself did not fail.
    Any doubt -> the new file is kept.
    """
    media_info = extract_multi_factor_signatures(message)
    if not media_info:
        return

    media_type, signatures, file_size = media_info
    chat_id = message.chat.id
    lock = chat_locks.setdefault(chat_id, asyncio.Lock())

    async with lock:  # find + save must be atomic, else two copies posted together both look "original"
        duplicate = await db.find_duplicate_by_signatures(chat_id, signatures)

        if duplicate and config.VERIFY_ORIGINAL:
            orig_id = duplicate.get("message_id")
            exists = await message_exists(chat_id, orig_id) if orig_id else False
            if exists is None:
                logger.warning(f"⚠️ [KEPT] Could not verify original {orig_id}; keeping msg {message.message_id}")
                return
            if exists is False:
                logger.info(f"♻️ Original {orig_id} no longer exists; msg {message.message_id} becomes new original")
                await db.delete_record(duplicate["_id"])
                duplicate = None

        if not duplicate:
            await db.save_media_signatures(
                chat_id=chat_id,
                signatures=signatures,
                media_type=media_type,
                file_size=file_size,
                message_id=message.message_id
            )
            logger.info(f"💾 [SAVED ORIGINAL] '{chat_title}' ({chat_id}) | {media_type} | Msg {message.message_id}")
            return

    orig_msg_id = duplicate.get("message_id")
    matched = next((sg for sg in signatures if sg in duplicate.get("sigs", [])), signatures[0])
    logger.info(f"🗑️ [DUPLICATE] '{chat_title}' ({chat_id}) | {media_type} | Msg {message.message_id} "
                f"is copy of {orig_msg_id} via {factor_of(matched)}")

    if config.DELAY > 0:
        await asyncio.sleep(config.DELAY)

    for attempt in range(2):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            await db.record_deletion(chat_id, media_type)
            await db.log_deletions(chat_id, [{
                "message_id": message.message_id, "original_id": orig_msg_id,
                "factor": factor_of(matched), "media_type": media_type, "source": "live"
            }])
            if config.SEND_DELETE_ALERT and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                try:
                    alert = await message.answer(f"⚠️ Duplicate {media_type} removed.")
                    await asyncio.sleep(5)
                    await alert.delete()
                except Exception:
                    pass
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            logger.warning(f"⚠️ Bot needs 'Delete Messages' permission in '{chat_title}' ({chat_id})!")
            return
        except TelegramBadRequest as e:
            logger.warning(f"⚠️ Cannot delete message {message.message_id}: {e.message}")
            return
        except Exception as e:
            logger.error(f"Unexpected error deleting {message.message_id}: {e}")
            return

# ----------------- CHANNEL POSTS HANDLER (AUTOMATIC REMOVAL) ----------------- #

@dp.channel_post()
async def handle_channel_post(message: types.Message):
    """
    Directly processes all posts in Telegram Channels.
    Detects and deletes duplicate photos, videos, audios, etc.
    Also handles /clean command posted in channels!
    """
    chat_id = message.chat.id
    chat_title = message.chat.title or "Channel"

    asyncio.create_task(db.register_chat(chat_id, chat_title, "channel"))

    # If admin posted /clean in channel (only admins can post in a channel)
    if message.text and message.text.strip().startswith("/clean"):
        args = message.text.strip().split()
        nums, dry = parse_clean_args(args)
        logger.info(f"⚡ Received /clean command in channel '{chat_title}' ({chat_id}) dry={dry}")
        try:
            await message.delete()
        except Exception:
            pass
        await execute_clean_task(
            target_chat_id=chat_id,
            reply_to_msg=message,
            count_limit=nums[0] if nums else None,
            dry_run=dry
        )
        return

    await process_media_message(message, chat_title)

# ----------------- GROUP MESSAGES HANDLER (AUTOMATIC REMOVAL) ----------------- #

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def handle_group_message(message: types.Message):
    """
    Processes all messages in Groups and Supergroups.
    Detects and deletes duplicate photos, videos, audios, etc.
    """
    if message.text and message.text.startswith("/"):
        return

    chat_id = message.chat.id
    chat_title = message.chat.title or "Group"

    asyncio.create_task(db.register_chat(chat_id, chat_title, str(message.chat.type)))

    await process_media_message(message, chat_title)

# ----------------- PRIVATE MEDIA HANDLER ----------------- #

@dp.message(F.chat.type == ChatType.PRIVATE)
async def handle_private_media(message: types.Message):
    """If user sends media in private DM, give helpful info."""
    media_info = extract_multi_factor_signatures(message)
    if media_info:
        media_type, signatures, file_size = media_info
        size_kb = round(file_size / 1024, 2)
        me = await bot.get_me()
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add to Channel", url=f"https://t.me/{me.username}?startchannel=true&admin=delete_messages")],
            [InlineKeyboardButton(text="➕ Add to Group", url=f"https://t.me/{me.username}?startgroup=true&admin=delete_messages")]
        ])
        await message.reply(
            f"📥 <b>Media Received in DM</b>\n\n"
            f"• <b>Type:</b> <code>{media_type}</code>\n"
            f"• <b>Size:</b> <code>{size_kb} KB</code>\n"
            f"• <b>Factors Extracted:</b> <code>{len(signatures)}</code>\n\n"
            f"💡 Send <code>/clean</code> in your channel to remove all old duplicates at once!",
            reply_markup=buttons
        )

# ----------------- MAIN RUNNER ----------------- #

async def main():
    logger.info("Initializing Duplicate Media Remover Bot (Multi-Factor Engine)...")

    # Connect to MongoDB
    await db.connect()

    # Get Bot details
    me = await bot.get_me()
    logger.info(f"🚀 Bot connected successfully as @{me.username} (ID: {me.id})")
    logger.info("Listening for Channel posts, Group messages, Multi-Factor /clean streaming...")

    # Start Polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Bot session closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")

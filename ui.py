"""All user-facing text and keyboards (English, HTML parse mode)."""
import html
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

import config
from duplicate_detector import FACTOR_NAMES

SEP = "━━━━━━━━━━━━━━━━━━━━"
PER_PAGE = 6


def e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def icon(ctype: str) -> str:
    return "📢" if ctype == "channel" else "👥"


def fmt_dt(dt) -> str:
    return dt.strftime("%d %b %Y, %H:%M UTC") if dt else "Never"


def short(text: str, n: int = 26) -> str:
    text = text or "Untitled"
    return text if len(text) <= n else text[: n - 1] + "…"


# ------------------------------------------------------------------ texts

def home_text(name: str) -> str:
    return (
        f"<b>🧹 Duplicate Media Remover</b>\n{SEP}\n"
        f"Hi <b>{e(name)}</b>! I remove duplicate photos, videos, audio and files from your "
        f"channels and groups, and I never touch the original.\n\n"
        f"<b>Get started</b>\n"
        f"1️⃣  Add me as admin with <i>Delete Messages</i>\n"
        f"2️⃣  Open <b>My Chats</b> and pick a chat\n"
        f"3️⃣  Run a <b>Dry Run</b>, then <b>Clean</b>\n\n"
        f"🔒 <i>Your chats are private. Other users can never see or clean them.</i>"
    )


def help_text() -> str:
    return (
        f"<b>📖 Help</b>\n{SEP}\n"
        f"<b>Setup</b>\n"
        f"• Add the bot to your channel/group as admin\n"
        f"• Enable <i>Delete Messages</i> and save\n"
        f"• New duplicates are then removed automatically\n\n"
        f"<b>Cleaning old history</b>\n"
        f"• Open <b>My Chats</b> ➜ choose a chat ➜ <b>Dry Run</b>\n"
        f"• Review the report, then press <b>Clean</b>\n"
        f"• Or send <code>/clean</code> in the chat itself\n\n"
        f"<b>What counts as a duplicate?</b>\n"
        f"• 🆔 Same Telegram file (copy / forward)\n"
        f"• ⚖️ Exact same size <b>and</b> resolution <b>and</b> duration\n"
        f"• The oldest copy always stays. Weak matches never delete anything.\n\n"
        f"<b>Privacy &amp; storage</b>\n"
        f"• History scans run in memory and store nothing\n"
        f"• Temporary live-detection data is wiped after every clean and expires automatically"
    )


def about_text() -> str:
    return (
        f"<b>ℹ️ About</b>\n{SEP}\n"
        f"<b>Duplicate Media Remover</b>\n"
        f"Safe multi-factor duplicate detection for Telegram channels and groups.\n\n"
        f"🛡 Originals protected\n"
        f"🔒 Per-user private chat list\n"
        f"💾 Minimal database footprint\n"
        f"🟢 Built to run 24/7"
    )


def commands_text() -> str:
    return (
        f"<b>⌨️ Commands</b>\n{SEP}\n"
        f"<b>Private chat</b>\n"
        f"/menu – main menu\n"
        f"/chats – your chats\n"
        f"/clean – pick a chat to clean\n"
        f"/clean <code>&lt;chat_id&gt; [dry] [N]</code> – clean a specific chat\n"
        f"/reset <code>&lt;chat_id&gt;</code> – wipe stored data of a chat\n"
        f"/stats – your statistics\n"
        f"/ping – bot status\n\n"
        f"<b>Groups &amp; channels (admins)</b>\n"
        f"/clean [dry] [N] – clean history (dry = report only)\n"
        f"/reset – wipe stored data\n"
        f"/check – reply to media: duplicate or original?\n"
        f"/del – reply to a message to delete it\n"
        f"/purge <code>N</code> – delete last N messages"
    )


def group_help_text() -> str:
    return (
        f"<b>🧹 Duplicate Media Remover</b>\n{SEP}\n"
        f"I remove duplicate media here automatically.\n\n"
        f"/clean <code>dry</code> – preview duplicates\n"
        f"/clean – clean the history\n"
        f"/check – check a replied media\n"
        f"/reset – wipe stored data\n\n"
        f"<i>Admins only. Make sure I have <b>Delete Messages</b> permission.</i>"
    )


def chats_text(total: int, page: int) -> str:
    pages = max(1, -(-total // PER_PAGE))
    if total == 0:
        return (
            f"<b>📂 My Chats</b>\n{SEP}\n"
            f"No chats yet.\n\n"
            f"Add me as admin to a channel or group and it will appear here. "
            f"Already added? Use <code>/clean &lt;chat_id&gt;</code>."
        )
    return (
        f"<b>📂 My Chats</b>  ·  {total} total\n{SEP}\n"
        f"Choose a chat to manage.  <i>Page {page + 1}/{pages}</i>"
    )


def panel_text(chat: Dict[str, Any], records: int) -> str:
    return (
        f"<b>{icon(chat.get('type'))} {e(chat.get('title'))}</b>\n{SEP}\n"
        f"🆔 <code>{chat['chat_id']}</code>\n"
        f"🗑 Duplicates removed: <b>{chat.get('deleted', 0)}</b>\n"
        f"🕘 Last clean: {fmt_dt(chat.get('last_clean'))}\n"
        f"💾 Temporary records: <b>{records}</b>\n\n"
        f"<i>Tip: run a Dry Run first. Nothing is deleted until you confirm.</i>"
    )


def confirm_text(chat: Dict[str, Any], limit: int) -> str:
    scope = f"the last <b>{limit}</b> messages" if limit else "the <b>full history</b>"
    return (
        f"<b>⚠️ Confirm clean</b>\n{SEP}\n"
        f"Chat: <b>{e(chat.get('title'))}</b>\n"
        f"Scope: {scope}\n\n"
        f"Duplicates will be deleted permanently. The oldest copy of every file stays.\n"
        f"<i>Not sure? Run a Dry Run first.</i>"
    )


def reset_text(chat: Dict[str, Any], records: int) -> str:
    return (
        f"<b>🗑 Reset stored data</b>\n{SEP}\n"
        f"Chat: <b>{e(chat.get('title'))}</b>\n"
        f"This removes <b>{records}</b> temporary records. No messages are deleted.\n"
        f"Live detection will restart from new posts."
    )


def progress_text(done: int, total: int, media: int, found: int, dry: bool) -> str:
    pct = min(round(done / total * 100), 100) if total else 100
    filled = pct // 10
    bar = "█" * filled + "░" * (10 - filled)
    title = "🧪 Dry run in progress" if dry else "🧹 Cleaning in progress"
    return (
        f"<b>{title}</b>\n{SEP}\n"
        f"<code>{bar}</code>  {pct}%\n\n"
        f"📨 Scanned: <b>{done}/{total}</b>\n"
        f"📁 Media found: <b>{media}</b>\n"
        f"{'🔍 Duplicates found' if dry else '🗑 Deleted'}: <b>{found}</b>"
    )


def starting_text(dry: bool) -> str:
    return f"<b>{'🧪 Dry run' if dry else '🧹 Clean'} starting…</b>\n<i>Preparing scan</i>"


def report_text(res: Dict[str, Any], title: str, wiped: int = 0) -> str:
    types = res.get("type_counts", {})
    factors = res.get("factor_counts", {})
    t_lines = "\n".join(f"  • {k.title()}: {v}" for k, v in types.items()) or "  • none"
    f_lines = "\n".join(f"  • {FACTOR_NAMES.get(k, k)}: {v}" for k, v in factors.items()) or "  • none"
    head = "🧪 Dry run complete" if res["dry_run"] else ("⏹ Clean stopped" if res.get("cancelled") else "✅ Clean complete")
    key = "Would be deleted" if res["dry_run"] else "Deleted"
    count = res["duplicates_flagged"] if res["dry_run"] else res["duplicates_deleted"]
    text = (
        f"<b>{head}</b>\n{SEP}\n"
        f"{e(title)}\n\n"
        f"📨 Scanned: <b>{res['total_scanned']}</b>\n"
        f"📁 Media: <b>{res['total_media']}</b>\n"
        f"🗑 {key}: <b>{count}</b>\n"
        f"💎 Originals kept: <b>{res['originals_kept']}</b>\n"
        f"⏱ Time: <b>{res['elapsed_seconds']}s</b>\n\n"
        f"<b>Types</b>\n{t_lines}\n\n<b>Matched by</b>\n{f_lines}"
    )
    if res["dry_run"] and res.get("preview"):
        sample = "\n".join(f"<code>{e(p)}</code>" for p in res["preview"][:8])
        text += f"\n\n<b>Sample</b> (duplicate ➜ original)\n{sample}"
    if not res["dry_run"]:
        text += f"\n\n💾 <i>Temporary data cleared ({wiped} records).</i>"
    return text


def stats_text(mine: Dict[str, Any], glob: Optional[Dict[str, Any]] = None) -> str:
    text = (
        f"<b>📊 Your Statistics</b>\n{SEP}\n"
        f"📂 Chats: <b>{mine.get('chats', 0)}</b>\n"
        f"🗑 Duplicates removed: <b>{mine.get('deleted', 0)}</b>"
    )
    if glob:
        size = f"{glob['size_mb']} MB" if glob["size_mb"] >= 0 else "n/a"
        text += (
            f"\n\n<b>🌐 Global (owner only)</b>\n"
            f"👤 Users: <b>{glob['users']}</b>\n"
            f"📂 Chats: <b>{glob['chats']}</b>\n"
            f"🗑 Removed: <b>{glob['deleted']}</b>\n"
            f"💾 Records: <b>{glob['records']}</b>\n"
            f"🗄 Database: <b>{size}</b> / 512 MB"
        )
    return text


def group_stats_text(chat: Dict[str, Any], records: int) -> str:
    return (
        f"<b>📊 Chat statistics</b>\n{SEP}\n"
        f"🗑 Duplicates removed: <b>{chat.get('deleted', 0)}</b>\n"
        f"💾 Temporary records: <b>{records}</b>\n"
        f"🕘 Last clean: {fmt_dt(chat.get('last_clean'))}"
    )


# ------------------------------------------------------------------ keyboards

def home_kb(bot_username: str) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="📂 My Chats", callback_data="nav:chats:0"), Btn(text="📊 Stats", callback_data="nav:stats")],
        [Btn(text="📖 Help", callback_data="nav:help"), Btn(text="⌨️ Commands", callback_data="nav:cmds")],
        [Btn(text="➕ Add to Channel",
             url=f"https://t.me/{bot_username}?startchannel=true&admin=delete_messages+post_messages"),
         Btn(text="➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true&admin=delete_messages")],
        [Btn(text="ℹ️ About", callback_data="nav:about")],
    ])


def back_kb(target: str = "home") -> Markup:
    return Markup(inline_keyboard=[[Btn(text="« Back", callback_data=f"nav:{target}")]])


def chats_kb(chats: List[Dict[str, Any]], page: int, total: int) -> Markup:
    rows = [[Btn(text=f"{icon(c.get('type'))} {short(c.get('title'))}", callback_data=f"chat:{c['chat_id']}")]
            for c in chats]
    nav = []
    if page > 0:
        nav.append(Btn(text="‹ Prev", callback_data=f"nav:chats:{page - 1}"))
    if (page + 1) * PER_PAGE < total:
        nav.append(Btn(text="Next ›", callback_data=f"nav:chats:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([Btn(text="« Back", callback_data="nav:home")])
    return Markup(inline_keyboard=rows)


def panel_kb(chat_id: int) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="🧪 Dry Run (full history)", callback_data=f"dry:{chat_id}:0")],
        [Btn(text="🧹 Clean full history", callback_data=f"ask:{chat_id}:0")],
        [Btn(text="🕘 Last 500", callback_data=f"ask:{chat_id}:500"),
         Btn(text="🕘 Last 2000", callback_data=f"ask:{chat_id}:2000")],
        [Btn(text="🗑 Reset data", callback_data=f"rst:{chat_id}"),
         Btn(text="🔄 Refresh", callback_data=f"chat:{chat_id}")],
        [Btn(text="« My Chats", callback_data="nav:chats:0")],
    ])


def confirm_kb(chat_id: int, limit: int) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="✅ Yes, delete duplicates", callback_data=f"run:{chat_id}:{limit}")],
        [Btn(text="🧪 Dry run first", callback_data=f"dry:{chat_id}:{limit}"),
         Btn(text="✖ Cancel", callback_data=f"chat:{chat_id}")],
    ])


def reset_kb(chat_id: int) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="✅ Reset now", callback_data=f"rstok:{chat_id}"), Btn(text="✖ Cancel", callback_data=f"chat:{chat_id}")],
    ])


def progress_kb(chat_id: int) -> Markup:
    return Markup(inline_keyboard=[[Btn(text="⏹ Stop", callback_data=f"stop:{chat_id}")]])


def report_kb(chat_id: int, dry: bool, limit: int, has_dups: bool) -> Markup:
    rows = []
    if dry and has_dups:
        rows.append([Btn(text="✅ Delete these duplicates", callback_data=f"run:{chat_id}:{limit}")])
    rows.append([Btn(text="« Chat panel", callback_data=f"chat:{chat_id}")])
    return Markup(inline_keyboard=rows)


def open_panel_kb(bot_username: str, chat_id: int) -> Markup:
    return Markup(inline_keyboard=[[Btn(text="📂 Open panel", url=f"https://t.me/{bot_username}?start=chats")]])

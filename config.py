import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("true", "1", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _ids(*names: str):
    out = []
    for n in names:
        for x in os.getenv(n, "").split(","):
            x = x.strip()
            if x.lstrip("-").isdigit():
                out.append(int(x))
    return out


# ---------------- Telegram ---------------- #
API_ID = _int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Bot owners: see global stats, can manage any chat. Regular users only see THEIR OWN chats.
OWNER_IDS = _ids("OWNER_IDS", "AUTH_USERS")

# ---------------- MongoDB (free tier = 512 MB) ---------------- #
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "telegram_duplicate_remover")

# Live-detection records are temporary. They expire automatically.
RECORD_TTL_DAYS = _int("RECORD_TTL_DAYS", 7)
# Hard cap of stored records per chat (oldest removed first).
MAX_RECORDS_PER_CHAT = _int("MAX_RECORDS_PER_CHAT", 20000)
# If total DB size passes this, oldest records are purged globally.
DB_SOFT_LIMIT_MB = _int("DB_SOFT_LIMIT_MB", 380)
# Wipe a chat's stored records right after /clean finishes (history scan itself never uses the DB).
AUTO_WIPE_AFTER_CLEAN = _bool("AUTO_WIPE_AFTER_CLEAN", "True")
# Drop collections created by older versions of this bot (frees space).
DROP_LEGACY_DATA = _bool("DROP_LEGACY_DATA", "True")

# ---------------- Safety ---------------- #
MIN_FILE_SIZE = _int("MIN_FILE_SIZE", 20480)
VERIFY_ORIGINAL = _bool("VERIFY_ORIGINAL", "True")
DELAY = float(os.getenv("DELAY", 0))

# ---------------- Chat tidiness ---------------- #
# In private chat: after every Nth action, older bot/user messages are deleted.
TIDY_EVERY_N_ACTIONS = _int("TIDY_EVERY_N_ACTIONS", 3)
# In groups/channels: bot replies & the command message disappear after this many seconds (0 = keep).
GROUP_REPLY_TTL = _int("GROUP_REPLY_TTL", 60)

# ---------------- Uptime / keep-alive ---------------- #
PORT = _int("PORT", 8080)
# Public URL of this service (e.g. https://my-bot.onrender.com). Auto-detected on Render/Koyeb/Railway/Fly/Heroku.
KEEPALIVE_URL = os.getenv("KEEPALIVE_URL", "").strip()
PING_INTERVAL = _int("PING_INTERVAL", 240)  # seconds

# ---------------- Jobs ---------------- #
MAX_PARALLEL_CLEANS = _int("MAX_PARALLEL_CLEANS", 2)

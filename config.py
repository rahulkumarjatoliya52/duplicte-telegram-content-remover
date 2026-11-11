import os
from dotenv import load_dotenv

load_dotenv()

def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("true", "1", "yes")

# Telegram API credentials
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Authorized users (owner IDs, comma separated)
auth_users_raw = os.getenv("AUTH_USERS", "")
AUTH_USERS = [int(x.strip()) for x in auth_users_raw.split(",") if x.strip().isdigit()]

# MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "telegram_duplicate_remover")

# Optional buffer before deleting a live duplicate (seconds)
DELAY = float(os.getenv("DELAY", 0))

# Log a notice in groups after deleting (keep False for channels)
SEND_DELETE_ALERT = _bool("SEND_DELETE_ALERT", "False")

# ---------------- SAFETY SETTINGS ---------------- #

# Files smaller than this (bytes) are never size-matched (tiny files collide easily).
MIN_FILE_SIZE = int(os.getenv("MIN_FILE_SIZE", 20480))

# Before deleting a live duplicate, confirm the ORIGINAL message still exists.
# If it is gone (or cannot be verified), the new file is kept and becomes the new original.
VERIFY_ORIGINAL = _bool("VERIFY_ORIGINAL", "True")

# Keep an audit trail (original id -> deleted id -> reason) in MongoDB.
LOG_DELETIONS = _bool("LOG_DELETIONS", "True")

# Days to keep the audit trail.
DELETION_LOG_DAYS = int(os.getenv("DELETION_LOG_DAYS", 90))

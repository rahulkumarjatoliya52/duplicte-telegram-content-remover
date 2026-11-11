# 🤖 Telegram Duplicate Media Remover Bot (Safe Multi-Factor Edition)

A Telegram bot that, once made an admin in a **Channel** or **Group**, automatically detects and deletes duplicate **photos, videos, audio, documents, GIFs and voice notes** — without deleting originals by mistake.

---

## 🔍 Safe Detection

The old engine deleted a file if **any single weak factor** matched (for example only duration + resolution, or caption + dimensions). Many different videos share those, so originals were being deleted. Now only **two strong fingerprints** are used:

1. **Telegram Unique ID** – the same file (copy / forward).
2. **Exact Size + Resolution + Duration, all together** – for video. Photos use size + resolution, audio/voice use size + duration, documents use size + file name.

If any attribute is missing, no size-based fingerprint is created and the file is kept (fail-safe).

### Safety layers
- The **oldest** copy is always the original and is never deleted.
- Before a live delete, the bot **verifies the original message still exists**. If the original was deleted, or cannot be verified, the new file is **kept** and becomes the new original.
- Files smaller than `MIN_FILE_SIZE` are never size-matched.
- `/clean dry` – a test run that only reports; nothing is deleted.
- All admin commands are restricted to **AUTH_USERS and group admins**.
- Every deletion is logged in MongoDB (`deletion_log`): original id, deleted id, reason.

---

## ⚡ Fast Streaming Cleaner (`/clean`)

- **Real-time deletion:** duplicates are deleted batch by batch (100 messages at a time) while scanning.
- **Original safe:** the oldest file stays; only later copies are deleted. Run `/clean dry` first to preview.
- **Live progress:** the status message updates every 3 seconds.
- After a real clean, all kept originals are saved to the database so live detection knows them too.

---

## 📱 Commands

| Command | Where | Description |
|---|---|---|
| `/clean dry` | Channel / Group / DM | Test run: report only, nothing deleted |
| `/clean` | Channel / Group | Delete duplicates from the full history |
| `/clean <count>` | Channel / Group | Clean only the last N messages (e.g. `/clean 500`) |
| `/clean <channel_id> [dry]` | Private DM (owner) | Clean a channel from DM (e.g. `/clean -1004466397591 dry`) |
| `/del` or `/delete` | Group | Reply to a message to delete it (admins only) |
| `/purge <count>` | Group | Delete the last N messages (admins only) |
| `/check` | Group | Reply to media to see if it is a duplicate (admins only) |
| `/clean_db` | Group / Channel | Reset this chat's stored hashes (admins only) |
| `/start`, `/help`, `/about`, `/stats`, `/ping` | Anywhere | Menu, guide, info, statistics, latency |

---

## ⚙️ Configuration (`.env`)

Copy `.env.example` to `.env` and fill it in.

| Variable | Default | Meaning |
|---|---|---|
| `API_ID`, `API_HASH`, `BOT_TOKEN` | – | Telegram credentials |
| `AUTH_USERS` | – | Comma-separated owner user IDs |
| `MONGO_URI`, `DATABASE_NAME` | – | MongoDB connection |
| `DELAY` | `0` | Seconds to wait before deleting a live duplicate |
| `MIN_FILE_SIZE` | `20480` | Ignore size matching below this many bytes |
| `VERIFY_ORIGINAL` | `True` | Confirm the original exists before a live delete |
| `LOG_DELETIONS` / `DELETION_LOG_DAYS` | `True` / `90` | Audit trail |
| `SEND_DELETE_ALERT` | `False` | Post a notice in groups after a delete |

> ⚠️ Never commit or share your `.env`. If it was shared, rotate the bot token and database password.

---

## 🛠️ Run

```bash
pip install -r requirements.txt
python bot.py
```

or with Docker: `docker compose up -d --build`

**Recommended first run:** make the bot admin (with *Delete Messages*) in a test channel → `/clean_db` → `/clean dry` → review the report → `/clean`.

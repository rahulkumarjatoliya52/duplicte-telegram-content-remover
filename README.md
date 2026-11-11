# 🧹 Duplicate Media Remover Bot

Telegram bot that removes duplicate **photos, videos, audio, documents, GIFs and voice notes** from channels and groups, and never deletes the original.

## Highlights
- **Safe detection:** same Telegram file (unique ID) **or** exact size + resolution + duration together. Weak matches never delete anything. The oldest copy always stays.
- **Original check:** before a live delete, the bot verifies the original message still exists. Any doubt means the file is kept.
- **Private per user:** each user only sees chats they added/own. Others can never see or clean them.
- **Tiny database (512 MB friendly):** history scans run in memory and store nothing. Live-detection records are small integers that expire (TTL), are capped per chat, wiped after every clean, and removed when the bot leaves a chat. A global size guard purges the oldest data above `DB_SOFT_LIMIT_MB`.
- **Clean chats:** in private chat, every 3rd action deletes older messages above the newest one. In groups/channels, bot replies and command messages auto-disappear.
- **24/7 uptime:** built-in web server (`/health`), self-ping, auto-restart supervisor with backoff, Docker healthcheck.
- **Professional UI:** inline menus, chat panels, progress bar with ⏹ Stop, dry-run reports with a one-tap "Delete these duplicates", confirmation before any destructive action.

## Using it
1. Add the bot to your channel/group as admin with **Delete Messages**.
2. Open the bot in private chat ➜ **My Chats** ➜ pick the chat.
3. **Dry Run** ➜ review ➜ **Delete these duplicates** (or **Clean**).

Commands: `/menu /chats /clean /reset /stats /help /commands /about /ping`
In groups/channels (admins): `/clean [dry] [N]`, `/reset`, `/check`, `/del`, `/purge N`, `/stats`.

## Setup
```bash
cp .env.example .env     # fill API_ID, API_HASH, BOT_TOKEN, MONGO_URI, OWNER_IDS
pip install -r requirements.txt
python bot.py
```
Docker: `docker compose up -d --build`

## Keeping the bot awake on free hosts
The bot opens `PORT` and pings its own public URL every `PING_INTERVAL` seconds. On Render, Koyeb, Railway, Fly and Heroku the URL is detected automatically; elsewhere set `KEEPALIVE_URL`. For extra safety add a free external monitor (UptimeRobot or cron-job.org) hitting `https://YOUR-URL/health` every 5 minutes.

## Configuration
See `.env.example`. Important values: `RECORD_TTL_DAYS` (7), `MAX_RECORDS_PER_CHAT` (20000), `DB_SOFT_LIMIT_MB` (380), `AUTO_WIPE_AFTER_CLEAN` (True), `TIDY_EVERY_N_ACTIONS` (3), `GROUP_REPLY_TTL` (60).

> ⚠️ Never share your `.env`. If it was shared, rotate the bot token and database password.

"""
Keeps the bot awake 24/7 on hosts that sleep idle services (Render, Koyeb, Railway, Fly, Heroku...).

1. A tiny web server answers / and /health  (the platform needs an open port).
2. A self-ping task requests our own public URL every PING_INTERVAL seconds, so the platform
   always sees inbound traffic. Add an external monitor (UptimeRobot / cron-job.org) as a backup.
"""
import asyncio
import logging
import os
import time

import aiohttp
from aiohttp import web

import config

logger = logging.getLogger("DuplicateBot.KeepAlive")
START_TIME = time.time()
_state = {"db_ok": lambda: True}


def set_db_check(fn):
    _state["db_ok"] = fn


def public_url() -> str:
    url = config.KEEPALIVE_URL or os.getenv("RENDER_EXTERNAL_URL", "")
    if not url:
        for var, fmt in (("KOYEB_PUBLIC_DOMAIN", "https://{}"), ("RAILWAY_PUBLIC_DOMAIN", "https://{}"),
                         ("FLY_APP_NAME", "https://{}.fly.dev"), ("HEROKU_APP_NAME", "https://{}.herokuapp.com")):
            val = os.getenv(var, "")
            if val:
                url = fmt.format(val)
                break
    return url.rstrip("/")


async def _health(_request):
    return web.json_response({
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "database": bool(_state["db_ok"]()),
    })


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    logger.info(f"🌐 Health server listening on :{config.PORT}")
    return runner


async def self_ping_loop():
    url = public_url()
    if not url:
        logger.warning("No public URL found. Set KEEPALIVE_URL (or use an external uptime monitor) "
                       "so free hosts do not put the bot to sleep.")
        return
    logger.info(f"🔁 Self-ping every {config.PING_INTERVAL}s -> {url}/health")
    timeout = aiohttp.ClientTimeout(total=20)
    while True:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{url}/health") as r:
                    logger.debug(f"self-ping {r.status}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"self-ping failed: {e}")
        await asyncio.sleep(config.PING_INTERVAL)

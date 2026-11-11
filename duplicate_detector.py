"""
Strict duplicate fingerprints.

Old engine deleted a file if ANY single weak factor matched (e.g. same duration +
same resolution, or same caption + dimensions). Many different videos share those,
so originals got deleted.

New rule: only two kinds of fingerprints exist, and each is strong on its own:

  u2:<file_unique_id>
      Same Telegram file (forwarded / re-posted copy). Telegram guarantees this.

  f2:<type>:<exact size>:<ALL other attributes>
      Same exact byte size AND resolution AND duration (video), size+resolution
      (photo), size+duration (audio/voice), size+filename (document).
      Missing attribute -> no f2 fingerprint -> file is kept (fail-safe).
"""
import logging
from typing import List, Optional, Tuple

import config

logger = logging.getLogger("DuplicateBot.Detector")

FACTOR_NAMES = {
    "u2": "Telegram Unique ID",
    "f2": "Exact Size + Resolution/Duration/Name",
}


def build_signatures(
    media_type: str,
    unique_id: Optional[str],
    size: int,
    width: int = 0,
    height: int = 0,
    duration: int = 0,
    name: str = "",
) -> List[str]:
    sigs: List[str] = []
    size = size or 0
    width = width or 0
    height = height or 0
    duration = int(round(duration or 0))
    name = (name or "").strip().lower()

    if unique_id:
        sigs.append(f"u2:{unique_id}")

    if size < config.MIN_FILE_SIZE:
        return sigs

    if media_type in ("video", "animation", "video_note"):
        if width and height and duration:
            sigs.append(f"f2:{media_type}:{size}:{width}x{height}:{duration}")
    elif media_type == "photo":
        if width and height:
            sigs.append(f"f2:photo:{size}:{width}x{height}")
    elif media_type in ("audio", "voice"):
        if duration:
            sigs.append(f"f2:{media_type}:{size}:{duration}")
    elif media_type == "document":
        if len(name) > 3:
            sigs.append(f"f2:document:{size}:{name}")
    return sigs


def factor_of(sig: str) -> str:
    return sig.split(":", 1)[0]


def extract_multi_factor_signatures(message) -> Optional[Tuple[str, List[str], int]]:
    """aiogram Message -> (media_type, signatures, file_size) or None."""
    if message.video:
        m, t = message.video, "video"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.width, m.height, m.duration)
    elif message.photo:
        m, t = message.photo[-1], "photo"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.width, m.height)
    elif message.audio:
        m, t = message.audio, "audio"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, duration=m.duration)
    elif message.document:
        m, t = message.document, "document"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, name=m.file_name or "")
    elif message.animation:
        m, t = message.animation, "animation"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.width, m.height, m.duration)
    elif message.voice:
        m, t = message.voice, "voice"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, duration=m.duration)
    elif message.video_note:
        m, t = message.video_note, "video_note"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.length, m.length, m.duration)
    else:
        return None

    if not sigs:
        return None
    return t, sigs, (m.file_size or 0)


def extract_pyro_signatures(msg) -> Tuple[List[str], Optional[str], int]:
    """Pyrogram Message -> (signatures, media_type, file_size). Same rules as above."""
    if not msg or not msg.media:
        return [], None, 0

    if msg.video:
        m, t = msg.video, "video"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.width, m.height, m.duration)
    elif msg.photo:
        m, t = msg.photo, "photo"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.width, m.height)
    elif msg.audio:
        m, t = msg.audio, "audio"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, duration=m.duration)
    elif msg.document:
        m, t = msg.document, "document"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, name=m.file_name or "")
    elif msg.animation:
        m, t = msg.animation, "animation"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.width, m.height, m.duration)
    elif msg.voice:
        m, t = msg.voice, "voice"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, duration=m.duration)
    elif msg.video_note:
        m, t = msg.video_note, "video_note"
        sigs = build_signatures(t, m.file_unique_id, m.file_size, m.length, m.length, m.duration)
    else:
        return [], None, 0

    return sigs, t, (m.file_size or 0)

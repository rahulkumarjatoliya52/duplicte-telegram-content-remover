"""
Strict duplicate fingerprints (never delete on a weak match).

  u2  -> same Telegram file (copy / forward)  [file_unique_id]
  f2  -> exact size AND resolution AND duration (video) / size+resolution (photo) /
         size+duration (audio, voice) / size+file name (document)

Fingerprints are stored as 63-bit integers (not strings) to save database space.
"""
import hashlib
from typing import List, Optional, Tuple

import config

FACTOR_NAMES = {
    "u2": "Telegram unique ID",
    "f2": "Exact size + resolution/duration",
}

Fingerprint = Tuple[int, str]  # (hash, factor)


def _h(sig: str) -> int:
    return int.from_bytes(hashlib.blake2b(sig.encode(), digest_size=8).digest(), "big") >> 1


def build_signatures(media_type, unique_id, size, width=0, height=0, duration=0, name="") -> List[str]:
    sigs: List[str] = []
    size = size or 0
    width, height = width or 0, height or 0
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


def fingerprints(sigs: List[str]) -> List[Fingerprint]:
    return [(_h(s), s.split(":", 1)[0]) for s in sigs]


def _collect(m_type: str, m, kind: str):
    """Pull attributes from an aiogram/pyrogram media object."""
    uid = getattr(m, "file_unique_id", None)
    size = getattr(m, "file_size", 0) or 0
    w = getattr(m, "width", 0) or 0
    h = getattr(m, "height", 0) or 0
    d = getattr(m, "duration", 0) or 0
    name = getattr(m, "file_name", "") or ""
    if m_type == "video_note":
        w = h = getattr(m, "length", 0) or 0
    return build_signatures(m_type, uid, size, w, h, d, name), size


_ORDER = ("video", "photo", "audio", "document", "animation", "voice", "video_note")


def _pick(msg):
    for t in _ORDER:
        obj = getattr(msg, t, None)
        if obj:
            if t == "photo" and isinstance(obj, list):  # aiogram: list of sizes
                obj = obj[-1]
            return t, obj
    return None, None


def extract_multi_factor_signatures(message) -> Optional[Tuple[str, List[Fingerprint], int]]:
    """aiogram Message -> (media_type, fingerprints, size) or None."""
    t, obj = _pick(message)
    if not t:
        return None
    sigs, size = _collect(t, obj, "aiogram")
    return (t, fingerprints(sigs), size) if sigs else None


def extract_pyro_signatures(msg) -> Tuple[List[Fingerprint], Optional[str], int]:
    """Pyrogram Message -> (fingerprints, media_type, size). Same rules as above."""
    if not msg or not getattr(msg, "media", None):
        return [], None, 0
    t, obj = _pick(msg)
    if not t:
        return [], None, 0
    sigs, size = _collect(t, obj, "pyrogram")
    return fingerprints(sigs), t, size

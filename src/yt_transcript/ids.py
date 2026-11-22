import re
from urllib.parse import parse_qs, urlsplit

from .errors import invalid_video, validation

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

_PATH_PREFIXES = ("shorts", "embed", "live", "v")


def _split(value: str):
    if "://" not in value and "youtu" in value:
        value = "https://" + value
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host, parts


def parse_video_id(value: str) -> str:
    raw = value
    value = value.strip()
    if VIDEO_ID_RE.match(value):
        return value
    host, parts = _split(value)
    candidate = ""
    segments = [s for s in parts.path.split("/") if s]
    if host == "youtube.com":
        candidate = (parse_qs(parts.query).get("v") or [""])[0]
        if not candidate and len(segments) >= 2 and segments[0] in _PATH_PREFIXES:
            candidate = segments[1]
    elif host == "youtu.be" and segments:
        candidate = segments[0]
    if not VIDEO_ID_RE.match(candidate):
        raise invalid_video(raw)
    return candidate


def parse_playlist_id(value: str) -> str:
    value = value.strip()
    candidate = value
    if "/" in value or "?" in value or "youtu" in value:
        _, parts = _split(value)
        candidate = (parse_qs(parts.query).get("list") or [""])[0]
    if not PLAYLIST_ID_RE.match(candidate):
        raise validation("Invalid YouTube playlist URL or ID")
    return candidate

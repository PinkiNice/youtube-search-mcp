"""yt-dlp as a last-resort source of caption tracks (blocking; run in a thread)."""

from urllib.parse import parse_qs, urlsplit

import yt_dlp

from .captions import Track, VideoData
from .errors import (
    RetryableUpstream,
    age_restricted,
    live_not_started,
    upstream_error,
    video_unavailable,
)


def extract(video_id: str, proxy: str | None, cookies_file: str | None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 15,
        **({"proxy": proxy} if proxy else {}),
        **({"cookiefile": cookies_file} if cookies_file else {}),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def to_video_data(info: dict) -> VideoData:
    tracks: list[Track] = []
    seen: set[str] = set()
    for source, auto in (("subtitles", False), ("automatic_captions", True)):
        for key, entries in (info.get(source) or {}).items():
            for entry in entries or []:
                url = entry.get("url") or ""
                if entry.get("ext") != "json3":
                    continue
                if auto and "tlang=" in url:
                    continue
                query = parse_qs(urlsplit(url).query)
                lang = (query.get("lang") or [key])[0]
                is_asr = auto or (query.get("kind") or [""])[0] == "asr"
                vss_id = ("a." if is_asr else ".") + lang
                if vss_id in seen:
                    continue
                seen.add(vss_id)
                tracks.append(Track(lang, is_asr, entry.get("name") or key, vss_id, url))
    duration = info.get("duration")
    return VideoData(
        video_id=info.get("id") or "",
        title=info.get("title"),
        author=info.get("uploader"),
        channel_id=info.get("channel_id"),
        length_seconds=None if info.get("live_status") == "is_live" or not duration else int(duration),
        tracks=tracks,
    )


def classify_error(exc: Exception, vid: str) -> Exception:
    msg = str(exc).lower()
    if any(s in msg for s in ("not a bot", "429", "too many requests")):
        return RetryableUpstream("bot_check")
    if any(s in msg for s in ("confirm your age", "age-restricted", "inappropriate")):
        return age_restricted(vid)
    if any(s in msg for s in ("will begin", "premieres in", "live event")):
        return live_not_started(vid)
    if any(
        s in msg
        for s in ("private video", "members-only", "join this channel", "video unavailable", "not available")
    ):
        return video_unavailable(vid)
    first = (str(exc).strip().splitlines() or [""])[0]
    return upstream_error(f"yt-dlp: {first}")

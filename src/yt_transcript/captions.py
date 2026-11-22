"""Pure caption-track selection and transcript formatting (no I/O)."""

import json
import re
from dataclasses import asdict, dataclass

from .errors import validation

_LANG_RE = re.compile(r"^(asr|asr-[a-z]{2,3}(-[a-z0-9]{2,4})?|[a-z]{2,3}(-[a-z0-9]{2,4})?)$")


@dataclass
class Track:
    language_code: str
    is_asr: bool
    name: str
    vss_id: str
    url: str | None = None

    @property
    def base(self) -> str:
        return self.language_code.split("-")[0].lower()

    @property
    def code(self) -> str:
        return ("asr-" if self.is_asr else "") + self.base


@dataclass
class VideoData:
    video_id: str
    title: str | None
    author: str | None
    channel_id: str | None
    length_seconds: int | None
    tracks: list[Track]

    def to_cache(self) -> dict:
        d = asdict(self)
        for t in d["tracks"]:
            t.pop("url", None)
        return d

    @classmethod
    def from_cache(cls, d: dict) -> "VideoData":
        return cls(**{**d, "tracks": [Track(**t) for t in d["tracks"]]})


def _name(node: dict | None) -> str:
    node = node or {}
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def tracks_from_player(player: dict) -> list[Track]:
    renderer = (player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}
    tracks = []
    for t in renderer.get("captionTracks") or []:
        lang = t.get("languageCode") or ""
        is_asr = t.get("kind") == "asr"
        tracks.append(
            Track(
                language_code=lang,
                is_asr=is_asr,
                name=_name(t.get("name")),
                vss_id=t.get("vssId") or (("a." if is_asr else ".") + lang),
                url=t.get("baseUrl"),
            )
        )
    return tracks


def video_data_from_player(player: dict, tracks: list[Track]) -> VideoData:
    vd = player.get("videoDetails") or {}
    raw_len = vd.get("lengthSeconds")
    length = None if vd.get("isLive") or raw_len in (None, "", "0") else int(raw_len)
    return VideoData(
        video_id=vd.get("videoId") or "",
        title=vd.get("title"),
        author=vd.get("author"),
        channel_id=vd.get("channelId"),
        length_seconds=length,
        tracks=tracks,
    )


def available_languages(tracks: list[Track]) -> list[dict]:
    manual_bases = {t.base for t in tracks if not t.is_asr}
    out: list[dict] = []
    seen: set[str] = set()

    def add(code: str, name: str, kind: str) -> None:
        if code not in seen:
            seen.add(code)
            out.append({"code": code, "name": name, "kind": kind})

    for t in tracks:
        if t.is_asr:
            add("asr-" + t.base, t.name, "auto")
            if t.base not in manual_bases:
                add(t.base, t.name, "auto")
        else:
            add(t.base, t.name, "manual")
    return out


def resolve_track(tracks: list[Track], requested: list[str] | None, fallback: bool) -> Track | None:
    if requested is None:
        hit = resolve_track(tracks, ["en"], True)
        if hit:
            return hit
        manual = [t for t in tracks if not t.is_asr]
        if manual:
            return manual[0]
        return tracks[0] if tracks else None
    for code in requested:
        code = code.lower()
        if code == "asr":
            hit = next((t for t in tracks if t.is_asr), None)
        elif code.startswith("asr-"):
            base = code[4:].split("-")[0]
            hit = next((t for t in tracks if t.is_asr and t.base == base), None)
        else:
            base = code.split("-")[0]
            hit = next((t for t in tracks if not t.is_asr and t.base == base), None)
            if hit is None and fallback:
                hit = next((t for t in tracks if t.is_asr and t.base == base), None)
        if hit:
            return hit
    return None


def parse_language_param(raw: str | None) -> list[str] | None:
    if raw is None or not raw.strip():
        return None
    codes = [c.strip().lower() for c in raw.split(",") if c.strip()]
    if not codes:
        return None
    if len(codes) > 10:
        raise validation("At most 10 language codes are allowed")
    for code in codes:
        if not _LANG_RE.match(code):
            raise validation(f"Invalid language code: {code}")
    return codes


def json3_url(base_url: str) -> str:
    return re.sub(r"&fmt=[^&]*", "", base_url) + "&fmt=json3"


def parse_json3(body: str) -> list[dict]:
    data = json.loads(body)
    segments = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not text:
            continue
        segments.append(
            {
                "text": text,
                "start": round(ev.get("tStartMs", 0) / 1000, 3),
                "duration": round((ev.get("dDurationMs") or 0) / 1000, 3),
            }
        )
    return segments


def length_text(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_transcript(segments: list[dict], fmt: str, include_timestamp: bool):
    if fmt == "json":
        return segments if include_timestamp else [{"text": s["text"]} for s in segments]
    if include_timestamp:
        return "\n".join(f"[{round(s['start'], 2)}s] {s['text']}" for s in segments)
    return " ".join(s["text"] for s in segments)

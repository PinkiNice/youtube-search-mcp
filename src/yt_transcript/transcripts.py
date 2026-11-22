import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import yt_dlp

from . import ytdlp_fallback
from .cache import TTL_INFO, TTL_TRANSCRIPT, Cache
from .captions import (
    Track,
    VideoData,
    available_languages,
    format_transcript,
    json3_url,
    length_text,
    parse_json3,
    resolve_track,
    tracks_from_player,
    video_data_from_player,
)
from .config import Settings
from .errors import (
    ApiError,
    RetryableUpstream,
    no_captions,
    no_requested_languages,
    temporarily_blocked,
)
from .innertube import BROWSER_UA, InnerTube, classify_playability
from .upstream import Upstream

log = logging.getLogger(__name__)

# Retry reasons that bench a (client, proxy) pair for yt_client_cooldown_s.
_BENCH_REASONS = {"bot_check", "rate_limited", "reload_required"}


@dataclass
class FetchResult:
    data: VideoData
    track: Track | None
    segments: list[dict] | None
    served_by: str


@dataclass
class TranscriptResult:
    video_id: str
    language: str
    segments: list[dict]
    data: VideoData
    served_by: str
    cache_hit: bool

    def to_body(self, fmt: str, include_timestamp: bool, send_metadata: bool) -> dict:
        body = {
            "video_id": self.video_id,
            "language": self.language,
            "transcript": format_transcript(self.segments, fmt, include_timestamp),
        }
        if send_metadata:
            body["metadata"] = metadata(self.data)
        body["length_seconds"] = self.data.length_seconds
        body["lengthText"] = length_text(self.data.length_seconds)
        return body


class TranscriptService:
    def __init__(self, upstream: Upstream, innertube: InnerTube, settings: Settings, cache: Cache):
        self.upstream = upstream
        self.innertube = innertube
        self.settings = settings
        self.cache = cache
        # (client, proxy_idx) -> monotonic expiry of its bench
        self._cooldown: dict[tuple[str, int], float] = {}

    def _benched(self, key: tuple[str, int]) -> bool:
        return self._cooldown.get(key, 0.0) > time.monotonic()

    def cooldowns(self) -> list[dict]:
        now = time.monotonic()
        return [
            {"client": client, "proxy": proxy, "seconds_left": round(expiry - now)}
            for (client, proxy), expiry in self._cooldown.items()
            if expiry > now
        ]

    async def _download(self, track: Track, proxy_idx: int) -> list[dict]:
        resp = await self.upstream.request(
            "GET", json3_url(track.url), proxy_idx=proxy_idx, headers={"User-Agent": BROWSER_UA}
        )
        body = resp.text
        if not resp.is_success or not body.strip():
            # HTTP 200 with an empty body is YouTube's PO-token wall.
            raise RetryableUpstream("empty_body")
        return parse_json3(body)

    async def _attempt(
        self,
        video_id: str,
        client: str,
        proxy_idx: int,
        want_track: Callable[[list[Track]], Track | None] | None,
    ) -> FetchResult:
        player = await self.innertube.player(video_id, client, proxy_idx)
        classify_playability(player, video_id)
        tracks = tracks_from_player(player)
        data = video_data_from_player(player, tracks)
        data.video_id = video_id
        served_by = f"innertube:{client}"
        if want_track is None:
            return FetchResult(data, None, None, served_by)
        track = want_track(tracks)
        if track is None:
            return FetchResult(data, None, None, served_by)
        segments = await self._download(track, proxy_idx)
        return FetchResult(data, track, segments, served_by)

    async def _fetch_fresh(
        self, video_id: str, want_track: Callable[[list[Track]], Track | None] | None
    ) -> FetchResult:
        res = await self._fetch_upstream(video_id, want_track)
        await self.cache.set(f"info:{video_id}", res.data.to_cache(), TTL_INFO)
        return res

    async def _fetch_upstream(
        self, video_id: str, want_track: Callable[[list[Track]], Track | None] | None
    ) -> FetchResult:
        attempts = [(c, self.upstream.next_proxy_idx()) for c in self.settings.player_client_list]
        # Benched pairs go last but are still tried: a stale bench must never cause a hard failure.
        attempts.sort(key=self._benched)
        try:
            for client, proxy_idx in attempts:
                try:
                    return await self._attempt(video_id, client, proxy_idx, want_track)
                except RetryableUpstream as exc:
                    log.warning(
                        "player attempt failed client=%s proxy=%d reason=%s",
                        client, proxy_idx, exc.reason,
                    )
                    if exc.reason in _BENCH_REASONS:
                        self._cooldown[(client, proxy_idx)] = (
                            time.monotonic() + self.settings.yt_client_cooldown_s
                        )
        except ApiError as err:
            if not (err.code == "age_restricted" and self.settings.yt_cookies_file
                    and self.settings.ytdlp_fallback):
                raise
        if not self.settings.ytdlp_fallback:
            raise temporarily_blocked()
        return await self._fetch_ytdlp(video_id, want_track)

    async def _fetch_ytdlp(
        self, video_id: str, want_track: Callable[[list[Track]], Track | None] | None
    ) -> FetchResult:
        proxy_idx = self.upstream.next_proxy_idx()
        proxy = self.upstream.proxy_url(proxy_idx)
        cookies = self.settings.yt_cookies_file or None
        try:
            info = await self.upstream.throttled(
                lambda: asyncio.to_thread(ytdlp_fallback.extract, video_id, proxy, cookies)
            )
        except yt_dlp.utils.DownloadError as exc:
            err = ytdlp_fallback.classify_error(exc, video_id)
            if isinstance(err, RetryableUpstream):
                raise temporarily_blocked() from exc
            raise err from exc
        data = ytdlp_fallback.to_video_data(info)
        data.video_id = video_id
        track = want_track(data.tracks) if want_track else None
        segments = None
        if track is not None:
            try:
                segments = await self._download(track, proxy_idx)
            except RetryableUpstream as exc:
                raise temporarily_blocked() from exc
        log.info("served via yt-dlp fallback video=%s", video_id)
        return FetchResult(data, track, segments, "yt-dlp")

    async def get_transcript(
        self, video_id: str, langs: list[str] | None, fallback: bool
    ) -> TranscriptResult:
        def want(tracks: list[Track]) -> Track | None:
            return resolve_track(tracks, langs, fallback)

        def missing(data: VideoData) -> ApiError:
            if langs is None:
                return no_captions(video_id)
            return no_requested_languages(langs, available_languages(data.tracks))

        cached_info = await self.cache.get(f"info:{video_id}")
        if cached_info is not None:
            data = VideoData.from_cache(cached_info)
            track = want(data.tracks)
            if track is None:
                raise missing(data)
            segments = await self.cache.get(f"tx:{video_id}:{track.vss_id}")
            if segments is not None:
                return TranscriptResult(video_id, track.code, segments, data, "cache", True)

        res = await self._fetch_fresh(video_id, want)
        if res.track is None:
            raise missing(res.data)
        segments = res.segments or []
        await self.cache.set(f"tx:{video_id}:{res.track.vss_id}", segments, TTL_TRANSCRIPT)
        return TranscriptResult(video_id, res.track.code, segments, res.data, res.served_by, False)

    async def get_info(self, video_id: str) -> tuple[dict, bool]:
        cached_info = await self.cache.get(f"info:{video_id}")
        hit = cached_info is not None
        data = VideoData.from_cache(cached_info) if hit else (await self._fetch_fresh(video_id, None)).data
        if not data.tracks:
            raise no_captions(video_id)
        return {
            "video_id": video_id,
            "metadata": metadata(data),
            "available_languages": available_languages(data.tracks),
        }, hit


def metadata(data: VideoData) -> dict:
    return {
        "title": data.title,
        "author_name": data.author,
        "author_url": f"https://www.youtube.com/channel/{data.channel_id}",
        "thumbnail_url": f"https://i.ytimg.com/vi/{data.video_id}/hqdefault.jpg",
    }

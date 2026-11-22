import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from .errors import (
    RetryableUpstream,
    age_restricted,
    live_not_started,
    temporarily_blocked,
    upstream_error,
    video_unavailable,
)
from .upstream import Upstream

log = logging.getLogger(__name__)

# Sleep after failed attempt 1 and 2; the 3rd failure gives up.
_RETRY_BACKOFF_S = (0.5, 1.0, None)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
WEB_CLIENT_VERSION = "2.20251120.01.00"

# Client versions verified live 2026-11-22. When YouTube stops serving captions to these,
# copy current values from yt-dlp's INNERTUBE_CLIENTS (yt_dlp/extractor/youtube/_base.py).
PLAYER_CLIENTS: dict[str, dict] = {
    "ANDROID": {
        "context": {
            "clientName": "ANDROID",
            "clientVersion": "20.10.38",
            "androidSdkVersion": 30,
            "hl": "en",
            "gl": "US",
        },
        "user_agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip",
    },
    "IOS": {
        "context": {
            "clientName": "IOS",
            "clientVersion": "20.10.4",
            "deviceMake": "Apple",
            "deviceModel": "iPhone16,2",
            "osName": "iPhone",
            "osVersion": "18.3.2.22D82",
            "hl": "en",
            "gl": "US",
        },
        "user_agent": "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
    },
    "ANDROID_VR": {
        "context": {
            "clientName": "ANDROID_VR",
            "clientVersion": "1.65.10",
            "deviceMake": "Oculus",
            "deviceModel": "Quest 3",
            "androidSdkVersion": 32,
            "osName": "Android",
            "osVersion": "12L",
            "hl": "en",
            "gl": "US",
        },
        "user_agent": (
            "com.google.android.apps.youtube.vr.oculus/1.65.10 "
            "(Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip"
        ),
    },
    "WEB": {
        "context": {"clientName": "WEB", "clientVersion": WEB_CLIENT_VERSION, "hl": "en", "gl": "US"},
        "user_agent": BROWSER_UA,
    },
}

_AGE_STATUSES = (
    "LOGIN_REQUIRED",
    "AGE_CHECK_REQUIRED",
    "AGE_VERIFICATION_REQUIRED",
    "CONTENT_CHECK_REQUIRED",
)


def validate_clients(names: list[str]) -> None:
    for name in names:
        if name not in PLAYER_CLIENTS:
            raise ValueError(f"Unknown InnerTube client: {name}")


def classify_playability(player: dict, video_id: str) -> None:
    ps = player.get("playabilityStatus") or {}
    status = ps.get("status")
    reason = (ps.get("reason") or "").lower()
    if status == "OK":
        return
    if "not a bot" in reason:
        raise RetryableUpstream("bot_check")
    if "reload" in reason:
        raise RetryableUpstream("reload_required")
    if status == "LIVE_STREAM_OFFLINE":
        raise live_not_started(video_id)
    if status in _AGE_STATUSES and ("age" in reason or "inappropriate" in reason):
        raise age_restricted(video_id)
    raise video_unavailable(video_id)


class InnerTube:
    def __init__(self, upstream: Upstream):
        self.upstream = upstream

    async def player(self, video_id: str, client_name: str, proxy_idx: int) -> dict:
        client = PLAYER_CLIENTS[client_name]
        resp = await self.upstream.request(
            "POST",
            "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
            proxy_idx=proxy_idx,
            headers={"Content-Type": "application/json", "User-Agent": client["user_agent"]},
            json={
                "context": {"client": client["context"]},
                "videoId": video_id,
                "contentCheckOk": True,
                "racyCheckOk": True,
            },
        )
        if not resp.is_success:
            raise upstream_error(f"player HTTP {resp.status_code}")
        return resp.json()

    async def web(self, endpoint: str, body: dict, proxy_idx: int) -> httpx.Response:
        return await self.upstream.request(
            "POST",
            f"https://www.youtube.com/youtubei/v1/{endpoint}?prettyPrint=false",
            proxy_idx=proxy_idx,
            headers={
                "Content-Type": "application/json",
                "User-Agent": BROWSER_UA,
                "Origin": "https://www.youtube.com",
                "X-Youtube-Client-Name": "1",
                "X-Youtube-Client-Version": WEB_CLIENT_VERSION,
            },
            json={"context": {"client": PLAYER_CLIENTS["WEB"]["context"]}, **body},
        )

    async def _with_retry(self, send: Callable[[int], Awaitable[httpx.Response]]) -> httpx.Response:
        for attempt, backoff in enumerate(_RETRY_BACKOFF_S):
            try:
                return await send(self.upstream.next_proxy_idx())
            except RetryableUpstream as exc:
                log.warning("web attempt %d failed reason=%s", attempt + 1, exc.reason)
                if backoff is None:
                    raise temporarily_blocked() from exc
                await asyncio.sleep(backoff)
        raise AssertionError("unreachable")

    async def web_json_with_retry(self, endpoint: str, body: dict) -> tuple[int, dict | None]:
        resp = await self._with_retry(lambda idx: self.web(endpoint, body, idx))
        if resp.is_success:
            return resp.status_code, resp.json()
        if resp.status_code in (400, 404):
            return resp.status_code, None
        raise upstream_error(f"{endpoint} HTTP {resp.status_code}")

    async def get_text_with_retry(self, url: str) -> tuple[int, str]:
        resp = await self._with_retry(
            lambda idx: self.upstream.request(
                "GET", url, proxy_idx=idx, headers={"User-Agent": BROWSER_UA}
            )
        )
        if resp.is_success or resp.status_code in (400, 404):
            return resp.status_code, resp.text
        raise upstream_error(f"GET {url} HTTP {resp.status_code}")

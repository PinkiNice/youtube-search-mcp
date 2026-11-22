import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .errors import RetryableUpstream

log = logging.getLogger(__name__)
T = TypeVar("T")


class Upstream:
    """Single throttle point for all YouTube traffic: concurrency, pacing, proxy rotation."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        timeout = settings.yt_request_timeout_s
        if transport is not None:
            self._proxies: list[str | None] = [None]
            self._clients = [
                httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=True)
            ]
        else:
            self._proxies = settings.proxy_list
            self._clients = [
                httpx.AsyncClient(proxy=p, timeout=timeout, follow_redirects=True)
                for p in self._proxies
            ]
        self._sem = asyncio.Semaphore(settings.yt_max_concurrency)
        self._interval = settings.yt_min_interval_ms / 1000
        self._next_slot = 0.0
        self._lock = asyncio.Lock()
        self._rr = 0

    @property
    def proxy_count(self) -> int:
        return len(self._clients)

    def next_proxy_idx(self) -> int:
        idx = self._rr % len(self._clients)
        self._rr += 1
        return idx

    def proxy_url(self, idx: int) -> str | None:
        return self._proxies[idx]

    async def throttled(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._sem:
            async with self._lock:
                now = time.monotonic()
                wait = max(0.0, self._next_slot - now)
                self._next_slot = max(now, self._next_slot) + self._interval * random.uniform(1.0, 1.5)
            if wait:
                await asyncio.sleep(wait)
            return await fn()

    async def request(
        self,
        method: str,
        url: str,
        *,
        proxy_idx: int,
        headers: dict | None = None,
        json: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        client = self._clients[proxy_idx]

        async def send() -> httpx.Response:
            return await client.request(method, url, headers=headers, json=json, params=params)

        started = time.monotonic()
        parts = urlsplit(url)
        try:
            resp = await self.throttled(send)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            log.debug("%s %s%s proxy=%d network error: %r", method, parts.netloc, parts.path, proxy_idx, exc)
            raise RetryableUpstream("network") from exc
        log.debug(
            "%s %s%s proxy=%d status=%d %dms",
            method, parts.netloc, parts.path, proxy_idx, resp.status_code,
            (time.monotonic() - started) * 1000,
        )
        if resp.status_code == 429:
            raise RetryableUpstream("rate_limited")
        if resp.status_code >= 500:
            raise RetryableUpstream("http_5xx")
        return resp

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()

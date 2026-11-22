from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from .cache import Cache
from .config import Settings
from .innertube import InnerTube, validate_clients
from .listings import ListingService
from .transcripts import TranscriptService
from .upstream import Upstream


@dataclass
class Services:
    transcripts: TranscriptService
    listings: ListingService


@asynccontextmanager
async def open_services(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[Services]:
    """Build every YouTube-facing service; shared by the HTTP API, MCP server and CLI."""
    validate_clients(settings.player_client_list)
    upstream = Upstream(settings, transport)
    cache = Cache(settings.cache_path, settings.cache_enabled)
    innertube = InnerTube(upstream)
    try:
        yield Services(
            TranscriptService(upstream, innertube, settings, cache),
            ListingService(innertube, cache),
        )
    finally:
        await upstream.aclose()
        cache.close()

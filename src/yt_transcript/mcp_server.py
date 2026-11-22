"""MCP (Model Context Protocol) server exposing the same operations as the HTTP API, over stdio."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .captions import parse_language_param
from .config import Settings
from .errors import ApiError
from .ids import parse_video_id
from .services import Services, open_services

INSTRUCTIONS = """\
YouTube transcripts, video info, search, channel and playlist listings, fetched directly from \
YouTube by a server running on the user's machine (no API key, no credits).
- Videos, channels and playlists accept URLs or IDs (`dQw4w9WgXcQ`, `@TED`, `UC…`, `PL…`).
- Listing tools return `continuation_token`; pass it back as `continuation` (alone) for the next page.
- Errors come back as JSON `{"detail", "code"}`. `temporarily_blocked` means YouTube throttled \
the request: wait a few seconds and retry. `no_transcript_for_requested_languages` lists \
`available_languages`.
- Results are cached locally, so repeating a call is cheap."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)

Video = Annotated[
    str, Field(description="YouTube video URL (watch, youtu.be, shorts, embed, live) or 11-character video ID")
]
Channel = Annotated[str, Field(description="Channel @handle, channel URL, or UC… channel ID")]
OptChannel = Annotated[
    str | None, Field(description="Channel @handle, channel URL, or UC… channel ID (omit when paging)")
]
Continuation = Annotated[
    str | None,
    Field(description="continuation_token from a previous page; pass it alone to fetch the next page"),
]


def create_server(settings: Settings) -> MCPServer:
    holder: dict[str, Services] = {}

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[None]:
        async with open_services(settings) as services:
            holder["services"] = services
            yield

    async def run(call: Callable[[Services], Awaitable[dict]]) -> dict:
        try:
            return await call(holder["services"])
        except ApiError as err:
            raise ToolError(json.dumps(err.body(), ensure_ascii=False)) from err

    mcp = MCPServer(
        name="yt-transcript",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        log_level=settings.log_level.upper(),
        lifespan=lifespan,
    )

    @mcp.tool(annotations=READ_ONLY)
    async def get_transcript(
        video: Video,
        language: Annotated[
            str | None,
            Field(
                description=(
                    "Comma-separated preferred languages, first available wins: `en`, `de`, `pt-br`. "
                    "`asr` = any auto-generated track, `asr-en` = auto-generated English. "
                    "Default: English, else the first manual track."
                )
            ),
        ] = None,
        language_fallback: Annotated[
            bool, Field(description="Allow auto-generated captions when no manual track matches a plain code")
        ] = True,
        format: Annotated[
            Literal["text", "json"],
            Field(description="`text`: one string; `json`: list of {text, start, duration} segments"),
        ] = "text",
        include_timestamp: Annotated[
            bool, Field(description="Prefix lines with `[12.3s]` (text) or keep start/duration (json)")
        ] = False,
        include_metadata: Annotated[bool, Field(description="Add title, author and thumbnail URL")] = False,
    ) -> dict:
        """Get the transcript (captions) of a YouTube video, with its language and duration."""

        async def call(s: Services) -> dict:
            vid = parse_video_id(video)
            res = await s.transcripts.get_transcript(vid, parse_language_param(language), language_fallback)
            return res.to_body(format, include_timestamp, include_metadata)

        return await run(call)

    @mcp.tool(annotations=READ_ONLY)
    async def get_video_info(video: Video) -> dict:
        """Get a video's title, author and thumbnail, plus the transcript languages it offers."""

        async def call(s: Services) -> dict:
            body, _ = await s.transcripts.get_info(parse_video_id(video))
            return body

        return await run(call)

    @mcp.tool(annotations=READ_ONLY)
    async def search_youtube(
        query: Annotated[str | None, Field(description="Search query (omit when paging)")] = None,
        type: Annotated[Literal["video", "channel", "playlist", "movie"], Field(description="Result type")] = "video",
        sort: Annotated[Literal["relevance", "views"], Field(description="Result order")] = "relevance",
        upload_date: Annotated[
            Literal["hour", "today", "week", "month", "year"] | None, Field(description="Videos only")
        ] = None,
        duration: Annotated[
            Literal["short", "medium", "long"] | None,
            Field(description="Videos only: short < 4 min, medium 4–20 min, long > 20 min"),
        ] = None,
        features: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Videos only, any of: hd, subtitles, cc, creative_commons, 3d, live, purchased, "
                    "4k, 360, location, hdr, vr180"
                )
            ),
        ] = None,
        continuation: Continuation = None,
    ) -> dict:
        """Search YouTube for videos, channels, playlists or movies."""
        feature_list = [f.strip().lower() for f in features or [] if f.strip()]
        return await run(
            lambda s: s.listings.search(
                query, type, sort, upload_date, duration, feature_list, None, continuation
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def resolve_channel(channel: Channel) -> dict:
        """Turn a channel @handle or URL into its UC… channel ID."""

        async def call(s: Services) -> dict:
            return {"channel_id": await s.listings.resolve_channel(channel), "resolved_from": channel}

        return await run(call)

    @mcp.tool(annotations=READ_ONLY)
    async def search_channel(
        channel: OptChannel = None,
        query: Annotated[str | None, Field(description="Search query (omit when paging)")] = None,
        continuation: Continuation = None,
    ) -> dict:
        """Search for videos inside one channel. Pass channel and query, or only continuation."""
        return await run(lambda s: s.listings.channel_search(channel, query, continuation))

    @mcp.tool(annotations=READ_ONLY)
    async def list_channel_videos(
        channel: OptChannel = None,
        tab: Annotated[Literal["videos", "shorts", "streams"], Field(description="Channel tab")] = "videos",
        sort: Annotated[
            Literal["latest", "popular", "oldest"] | None,
            Field(description="Default: newest first, 100 per page with channel totals in playlist_info"),
        ] = None,
        continuation: Continuation = None,
    ) -> dict:
        """List a channel's uploads, shorts or live streams, page by page."""
        return await run(lambda s: s.listings.channel_videos(channel, tab, sort, continuation))

    @mcp.tool(annotations=READ_ONLY)
    async def get_channel_latest(channel: Channel) -> dict:
        """Get a channel's 15 most recent uploads with exact publish dates and view counts (RSS feed)."""
        return await run(lambda s: s.listings.channel_latest(channel))

    @mcp.tool(annotations=READ_ONLY)
    async def list_playlist_videos(
        playlist: Annotated[
            str | None, Field(description="Playlist URL or PL… playlist ID (omit when paging)")
        ] = None,
        continuation: Continuation = None,
    ) -> dict:
        """List the videos in a playlist, page by page."""
        return await run(lambda s: s.listings.playlist_videos(playlist, continuation))

    return mcp

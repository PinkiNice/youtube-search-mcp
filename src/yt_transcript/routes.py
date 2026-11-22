from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from .captions import parse_language_param
from .ids import parse_video_id

router = APIRouter(prefix="/api/v2/youtube")


def _cache_header(hit: bool) -> str:
    return "HIT" if hit else "MISS"


@router.get("/transcript")
async def transcript(
    request: Request,
    response: Response,
    video_url: str,
    format: Literal["json", "text"] = "json",
    include_timestamp: bool = True,
    send_metadata: bool = False,
    language: str | None = None,
    language_fallback: bool = True,
) -> dict:
    vid = parse_video_id(video_url)
    langs = parse_language_param(language)
    res = await request.app.state.transcripts.get_transcript(vid, langs, language_fallback)
    body = res.to_body(format, include_timestamp, send_metadata)
    response.headers["X-Cache-Status"] = _cache_header(res.cache_hit)
    response.headers["X-Served-By"] = res.served_by
    return body


@router.get("/info")
async def info(request: Request, response: Response, video_url: str) -> dict:
    vid = parse_video_id(video_url)
    body, hit = await request.app.state.transcripts.get_info(vid)
    response.headers["X-Cache-Status"] = _cache_header(hit)
    return body


@router.get("/search")
async def search(
    request: Request,
    q: str | None = Query(None, min_length=1, max_length=200),
    type: Literal["video", "channel", "playlist", "movie"] = "video",
    sort: Literal["relevance", "views"] = "relevance",
    upload_date: Literal["hour", "today", "week", "month", "year"] | None = None,
    duration: Literal["short", "medium", "long"] | None = None,
    features: str | None = None,
    sp: str | None = None,
    continuation: str | None = None,
) -> dict:
    feature_list = [f.strip().lower() for f in (features or "").split(",") if f.strip()]
    return await request.app.state.listings.search(
        q, type, sort, upload_date, duration, feature_list, sp, continuation
    )


@router.get("/channel/resolve")
async def channel_resolve(request: Request, input: str = Query(..., min_length=1, max_length=200)) -> dict:
    return {
        "channel_id": await request.app.state.listings.resolve_channel(input),
        "resolved_from": input,
    }


@router.get("/channel/search")
async def channel_search(
    request: Request,
    channel: str | None = Query(None, min_length=1, max_length=200),
    q: str | None = Query(None, min_length=1, max_length=200),
    continuation: str | None = None,
) -> dict:
    return await request.app.state.listings.channel_search(channel, q, continuation)


@router.get("/channel/videos")
async def channel_videos(
    request: Request,
    channel: str | None = Query(None, min_length=1, max_length=200),
    tab: Literal["videos", "shorts", "streams"] = "videos",
    sort: Literal["latest", "popular", "oldest"] | None = None,
    continuation: str | None = None,
) -> dict:
    return await request.app.state.listings.channel_videos(channel, tab, sort, continuation)


@router.get("/channel/latest")
async def channel_latest(request: Request, channel: str = Query(..., min_length=1, max_length=200)) -> dict:
    return await request.app.state.listings.channel_latest(channel)


@router.get("/playlist/videos")
async def playlist_videos(
    request: Request,
    playlist: str | None = Query(None, min_length=1, max_length=300),
    continuation: str | None = None,
) -> dict:
    return await request.app.state.listings.playlist_videos(playlist, continuation)

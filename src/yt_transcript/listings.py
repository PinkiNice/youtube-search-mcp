"""Search, channel and playlist listings over the WEB InnerTube API and channel RSS."""

import base64
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import urlsplit

from . import parsers
from .cache import TTL_CHANNEL, TTL_PLAYLIST, TTL_RESOLVE, TTL_RSS, TTL_SEARCH, Cache
from .errors import (
    ApiError,
    channel_not_found,
    invalid_continuation,
    playlist_not_found,
    upstream_error,
    validation,
)
from .ids import CHANNEL_ID_RE, parse_playlist_id
from .innertube import InnerTube


def _exactly_one(message: str, *values) -> None:
    if sum(v is not None for v in values) != 1:
        raise validation(message)


SEARCH_TYPES = {"video": 1, "channel": 2, "playlist": 3, "movie": 4}
UPLOAD_DATES = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
DURATIONS = {"short": 1, "long": 2, "medium": 3}
FEATURES = {
    "hd": 4, "subtitles": 5, "cc": 5, "creative_commons": 6, "3d": 7, "live": 8,
    "purchased": 9, "4k": 14, "360": 15, "location": 23, "hdr": 25, "vr180": 26,
}
TAB_PARAMS = {
    "videos": "EgZ2aWRlb3PyBgQKAjoA",
    "shorts": "EgZzaG9ydHPyBgUKA5oBAA==",
    "streams": "EgdzdHJlYW1z8gYECgJ6AA==",
}
CHANNEL_SEARCH_PARAMS = "EgZzZWFyY2jyBgQKAloA"
SORT_CHIPS = {"latest": "Latest", "popular": "Popular", "oldest": "Oldest"}

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def search_params(
    type: str, sort: str | None, upload_date: str | None, duration: str | None, features: list[str]
) -> str:
    """Encode YouTube's search filter protobuf (the `sp` URL parameter)."""
    fields: dict[int, int] = {2: SEARCH_TYPES[type]}
    if upload_date:
        fields[1] = UPLOAD_DATES[upload_date]
    if duration:
        fields[3] = DURATIONS[duration]
    for feature in features:
        fields[FEATURES[feature]] = 1
    filters = b"".join(_field_varint(f, v) for f, v in sorted(fields.items()))
    top = b""
    if sort == "views":
        top += _field_varint(1, 3)
    top += _varint(2 << 3 | 2) + _varint(len(filters)) + filters
    return base64.b64encode(top).decode()


def _page(resp: dict, parse) -> tuple[list[dict], str | None]:
    results: list[dict] = []
    token = None
    for kind, node in parsers.iter_items(resp):
        if kind == "continuation":
            token = node
            continue
        item = parse(kind, node)
        if item is not None:
            results.append(item)
    return results, token


def _search_item(kind: str, node: dict) -> dict | None:
    if kind == "videoRenderer":
        return parsers.video_renderer(node)
    if kind == "channelRenderer":
        return parsers.channel_renderer(node)
    if kind == "movieRenderer":
        return parsers.movie_renderer(node)
    if kind == "lockupViewModel":
        item = parsers.lockup(node)
        return item if item and item.get("type") == "playlist" else None
    return None


def _video_only(kind: str, node: dict) -> dict | None:
    return parsers.video_renderer(node) if kind == "videoRenderer" else None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ListingService:
    def __init__(self, innertube: InnerTube, cache: Cache):
        self.innertube = innertube
        self.cache = cache

    async def _cached(self, endpoint: str, params: dict, ttl: float, build) -> dict:
        key = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        hit = await self.cache.get(key)
        if hit is not None:
            return hit
        value = await build()
        await self.cache.set(key, value, ttl)
        return value

    async def _browse_continuation(self, continuation: str) -> dict:
        status, resp = await self.innertube.web_json_with_retry("browse", {"continuation": continuation})
        if resp is None:
            raise invalid_continuation()
        return resp

    # -- search -------------------------------------------------------------------------

    async def search(
        self,
        q: str | None,
        type: str,
        sort: str,
        upload_date: str | None,
        duration: str | None,
        features: list[str],
        sp: str | None,
        continuation: str | None,
    ) -> dict:
        _exactly_one("Provide exactly one of q or continuation", q, continuation)
        for f in features:
            if f not in FEATURES:
                raise validation(f"Unknown feature: {f}")
        if type != "video" and (upload_date or duration or features):
            raise validation("upload_date, duration and features are only supported with type=video")
        params = {
            "q": q, "type": type, "sort": sort, "upload_date": upload_date, "duration": duration,
            "features": features, "sp": sp, "continuation": continuation,
        }

        async def build() -> dict:
            if continuation:
                body = {"continuation": continuation}
            else:
                body = {"query": q, "params": sp or search_params(type, sort, upload_date, duration, features)}
            status, resp = await self.innertube.web_json_with_retry("search", body)
            if resp is None:
                if continuation:
                    raise invalid_continuation()
                raise upstream_error(f"search HTTP {status}")
            results, token = _page(resp, _search_item)
            return {
                "results": results,
                "result_count": len(results),
                "continuation_token": token,
                "has_more": token is not None,
            }

        return await self._cached("search", params, TTL_SEARCH, build)

    # -- channels -----------------------------------------------------------------------

    async def resolve_channel(self, value: str) -> str:
        value = value.strip()
        if CHANNEL_ID_RE.match(value):
            return value
        parts = urlsplit(value if "://" in value else "https://" + value)
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 2 and segments[0] == "channel" and CHANNEL_ID_RE.match(segments[1]):
            return segments[1]

        async def build() -> dict:
            if value.startswith("http"):
                url = value
            else:
                url = "https://www.youtube.com/" + (value if value.startswith("@") else "@" + value)
            _, resp = await self.innertube.web_json_with_retry("navigation/resolve_url", {"url": url})
            browse_id = parsers.dig(resp, "endpoint", "browseEndpoint", "browseId") or ""
            if not browse_id.startswith("UC"):
                raise channel_not_found(value)
            return {"channel_id": browse_id}

        return (await self._cached("resolve", {"input": value}, TTL_RESOLVE, build))["channel_id"]

    async def channel_search(self, channel: str | None, q: str | None, continuation: str | None) -> dict:
        by_query = channel is not None and q is not None and continuation is None
        by_token = continuation is not None and channel is None and q is None
        if not (by_query or by_token):
            raise validation("Provide either channel and q, or continuation")

        async def build() -> dict:
            if continuation:
                resp = await self._browse_continuation(continuation)
            else:
                cid = await self.resolve_channel(channel)
                _, resp = await self.innertube.web_json_with_retry(
                    "browse", {"browseId": cid, "params": CHANNEL_SEARCH_PARAMS, "query": q}
                )
                if resp is None:
                    raise channel_not_found(channel)
            results, token = _page(resp, _video_only)
            return {
                "results": results,
                "result_count": len(results),
                "continuation_token": token,
                "has_more": token is not None,
            }

        params = {"channel": channel, "q": q, "continuation": continuation}
        return await self._cached("channel_search", params, TTL_CHANNEL, build)

    async def channel_videos(
        self, channel: str | None, tab: str, sort: str | None, continuation: str | None
    ) -> dict:
        _exactly_one("Provide exactly one of channel or continuation", channel, continuation)

        async def build() -> dict:
            info = None
            if continuation:
                resp = await self._browse_continuation(continuation)
            else:
                cid = await self.resolve_channel(channel)
                if tab == "videos" and sort is None:
                    # The uploads playlist pages 100 items at a time and carries totals.
                    _, resp = await self.innertube.web_json_with_retry(
                        "browse", {"browseId": "VLUU" + cid[2:]}
                    )
                    if resp is None:
                        raise channel_not_found(channel)
                    info = parsers.playlist_info(resp)
                else:
                    _, resp = await self.innertube.web_json_with_retry(
                        "browse", {"browseId": cid, "params": TAB_PARAMS[tab]}
                    )
                    if resp is None:
                        raise channel_not_found(channel)
                    if sort is not None:
                        resp = await self._sorted(resp, tab, sort)
            results, token = _page(resp, parsers.feed_item)
            return {
                "results": results,
                "playlist_info": info,
                "continuation_token": token,
                "has_more": token is not None,
            }

        params = {"channel": channel, "tab": tab, "sort": sort, "continuation": continuation}
        return await self._cached("channel_videos", params, TTL_CHANNEL, build)

    async def _sorted(self, resp: dict, tab: str, sort: str) -> dict:
        label = SORT_CHIPS[sort]
        chip = next((c for c in parsers.find_all(resp, "chipViewModel") if c.get("text") == label), None)
        token = parsers.dig(chip, "tapCommand", "innertubeCommand", "continuationCommand", "token")
        if not token:
            raise ApiError(422, "sort_unavailable", f"Channel tab '{tab}' does not offer sort={sort}")
        return await self._browse_continuation(token)

    async def channel_latest(self, channel: str) -> dict:
        async def build() -> dict:
            cid = await self.resolve_channel(channel)
            status, body = await self.innertube.get_text_with_retry(
                f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            )
            if status in (400, 404):
                raise channel_not_found(channel)
            return parse_rss(body, cid)

        return await self._cached("channel_latest", {"channel": channel}, TTL_RSS, build)

    # -- playlists ----------------------------------------------------------------------

    async def playlist_videos(self, playlist: str | None, continuation: str | None) -> dict:
        _exactly_one("Provide exactly one of playlist or continuation", playlist, continuation)
        if playlist is not None:
            playlist = parse_playlist_id(playlist)

        async def build() -> dict:
            if continuation:
                resp = await self._browse_continuation(continuation)
                info = None
            else:
                _, resp = await self.innertube.web_json_with_retry("browse", {"browseId": "VL" + playlist})
                if resp is None:
                    raise playlist_not_found(playlist)
                info = parsers.playlist_info(resp)
            results, token = _page(resp, parsers.feed_item)
            if not continuation and not results and info is None:
                raise playlist_not_found(playlist)
            return {
                "results": results,
                "playlist_info": info,
                "continuation_token": token,
                "has_more": token is not None,
                "cached_at": _now_iso(),
            }

        params = {"playlist": playlist, "continuation": continuation}
        return await self._cached("playlist_videos", params, TTL_PLAYLIST, build)


def _attr_int(el: ET.Element | None, name: str) -> int | None:
    value = el.get(name) if el is not None else None
    return int(value) if value and value.isdigit() else None


def parse_rss(body: str, channel_id: str) -> dict:
    root = ET.fromstring(body)

    def t(el: ET.Element, path: str) -> str | None:
        found = el.find(path, _NS)
        return found.text if found is not None else None

    author = root.find("atom:author", _NS)
    alternate = root.find("atom:link[@rel='alternate']", _NS)
    results = []
    for entry in root.findall("atom:entry", _NS):
        group = entry.find("media:group", _NS)
        thumb = group.find("media:thumbnail", _NS) if group is not None else None
        community = group.find("media:community", _NS) if group is not None else None
        stats = community.find("media:statistics", _NS) if community is not None else None
        rating = community.find("media:starRating", _NS) if community is not None else None
        link = entry.find("atom:link[@rel='alternate']", _NS)
        entry_author = entry.find("atom:author", _NS)
        results.append(
            {
                "videoId": t(entry, "yt:videoId"),
                "title": t(entry, "atom:title"),
                "channelId": channel_id,
                "author": t(entry_author, "atom:name") if entry_author is not None else None,
                "published": t(entry, "atom:published"),
                "updated": t(entry, "atom:updated"),
                "link": link.get("href") if link is not None else None,
                "description": t(group, "media:description") if group is not None else None,
                "thumbnail": {
                    "url": thumb.get("url"),
                    "width": _attr_int(thumb, "width"),
                    "height": _attr_int(thumb, "height"),
                }
                if thumb is not None
                else None,
                "viewCount": _attr_int(stats, "views"),
                "starRating": {
                    "average": float(rating.get("average", 0)),
                    "count": _attr_int(rating, "count"),
                    "min": _attr_int(rating, "min"),
                    "max": _attr_int(rating, "max"),
                }
                if rating is not None
                else None,
            }
        )
    return {
        "channel": {
            "channelId": channel_id,
            "title": t(root, "atom:title"),
            "author": t(author, "atom:name") if author is not None else None,
            "url": alternate.get("href") if alternate is not None else None,
            "published": t(root, "atom:published"),
        },
        "results": results,
        "result_count": len(results),
    }

"""Pure parsers for WEB InnerTube search/browse responses (no I/O)."""

import re
from collections.abc import Iterator

ITEM_KINDS = {
    "videoRenderer",
    "channelRenderer",
    "movieRenderer",
    "lockupViewModel",
    "shortsLockupViewModel",
    "playlistVideoRenderer",
}
# Subtrees holding recommendations, shelves or page chrome, never the requested listing.
SKIP_KEYS = {
    "header",
    "topbar",
    "sidebar",
    "microformat",
    "metadata",
    "responseContext",
    "frameworkUpdates",
    "reelShelfRenderer",
    "shelfRenderer",
    "gridShelfViewModel",
    "horizontalCardListRenderer",
    "secondaryContents",
}
_VERIFIED = {"BADGE_STYLE_TYPE_VERIFIED", "BADGE_STYLE_TYPE_VERIFIED_ARTIST"}
_DURATION_RE = re.compile(r"^\d+(:\d{2})+$")


def text(node) -> str | None:
    if not isinstance(node, dict):
        return None
    if node.get("simpleText"):
        return node["simpleText"]
    if node.get("runs"):
        return "".join(r.get("text", "") for r in node["runs"]) or None
    return node.get("content") or None


def https(url: str | None) -> str | None:
    return "https:" + url if url and url.startswith("//") else url


def contains_text(node, needle: str) -> bool:
    if isinstance(node, str):
        return needle in node
    if isinstance(node, dict):
        return any(contains_text(v, needle) for v in node.values())
    if isinstance(node, list):
        return any(contains_text(v, needle) for v in node)
    return False


def find_all(node, key: str) -> Iterator:
    """Every value stored under `key` anywhere in `node`."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from find_all(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from find_all(v, key)


def dig(node, *path):
    for key in path:
        if isinstance(node, dict):
            node = node.get(key)
        elif isinstance(node, list) and isinstance(key, int) and -len(node) <= key < len(node):
            node = node[key]
        else:
            return None
    return node


def iter_items(node) -> Iterator[tuple[str, object]]:
    """DFS over a response yielding (kind, renderer) items and ("continuation", token)."""
    if isinstance(node, list):
        for v in node:
            yield from iter_items(v)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key in SKIP_KEYS:
            continue
        if key in ITEM_KINDS:
            yield key, value
        elif key == "continuationItemRenderer":
            token = dig(value, "continuationEndpoint", "continuationCommand", "token")
            if token:
                yield "continuation", token
        elif key == "continuationItemViewModel":
            token = dig(
                value, "continuationCommand", "innertubeCommand", "continuationCommand", "token"
            )
            if token:
                yield "continuation", token
        else:
            yield from iter_items(value)


def _thumbs(node) -> list[dict]:
    return [{**t, "url": https(t.get("url"))} for t in dig(node, "thumbnails") or []]


def video_renderer(v: dict) -> dict:
    byline = v.get("ownerText") or v.get("longBylineText") or v.get("shortBylineText") or {}
    run = (byline.get("runs") or [{}])[0]
    # Collaborations wrap each channel's browseEndpoint in a "Collaborators" dialog; take the first.
    browse = next(find_all(run.get("navigationEndpoint"), "browseEndpoint"), None) or {}
    base_url = browse.get("canonicalBaseUrl") or ""
    return {
        "type": "video",
        "videoId": v.get("videoId"),
        "title": text(v.get("title")),
        "channelId": browse.get("browseId"),
        "channelTitle": run.get("text"),
        "channelHandle": base_url[1:] if base_url.startswith("/@") else None,
        "channelVerified": any(
            dig(b, "metadataBadgeRenderer", "style") in _VERIFIED for b in v.get("ownerBadges") or []
        ),
        "lengthText": text(v.get("lengthText")),
        "viewCountText": text(v.get("viewCountText")),
        "publishedTimeText": text(v.get("publishedTimeText")),
        "hasCaptions": any(
            dig(b, "metadataBadgeRenderer", "label") == "CC" for b in v.get("badges") or []
        ),
        "thumbnails": _thumbs(v.get("thumbnail")),
    }


def channel_renderer(c: dict) -> dict:
    texts = [text(c.get("subscriberCountText")), text(c.get("videoCountText"))]
    handle = next((t for t in texts if t and t.startswith("@")), None)
    subscribers = next((t for t in texts if t and "subscriber" in t), None)
    cid = c.get("channelId")
    return {
        "type": "channel",
        "channelId": cid,
        "title": text(c.get("title")),
        "handle": handle,
        "url": f"https://www.youtube.com/{handle}" if handle else f"https://www.youtube.com/channel/{cid}",
        "description": text(c.get("descriptionSnippet")),
        "subscriberCount": subscribers,
        "verified": any(
            dig(b, "metadataBadgeRenderer", "style") in _VERIFIED for b in c.get("ownerBadges") or []
        ),
        "rssUrl": f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
        "thumbnails": _thumbs(c.get("thumbnail")),
    }


def movie_renderer(m: dict) -> dict:
    return {
        "type": "movie",
        "videoId": m.get("videoId"),
        "title": text(m.get("title")),
        "channelTitle": text(m.get("longBylineText")),
        "lengthText": text(m.get("lengthText")),
        "hasCaptions": any(
            dig(b, "metadataBadgeRenderer", "label") == "CC" for b in m.get("badges") or []
        ),
        "thumbnails": _thumbs(m.get("thumbnail")),
    }


def _feed_item(**fields) -> dict:
    item = dict.fromkeys(
        (
            "videoId", "title", "channelId", "channelTitle", "channelHandle", "lengthText",
            "viewCountText", "publishedTimeText", "thumbnails", "index",
        )
    )
    item["members_only"] = False
    item.update(fields)
    return item


def lockup(lv: dict) -> dict | None:
    md = dig(lv, "metadata", "lockupMetadataViewModel") or {}
    rows = [
        r
        for r in dig(md, "metadata", "contentMetadataViewModel", "metadataRows") or []
        if not r.get("isSpacerRow")
    ]
    channel_id = channel_title = channel_handle = None
    for row in rows:
        for part in row.get("metadataParts") or []:
            for run in dig(part, "text", "commandRuns") or []:
                browse = dig(run, "onTap", "innertubeCommand", "browseEndpoint")
                if browse and channel_id is None:
                    channel_id = browse.get("browseId")
                    channel_title = dig(part, "text", "content")
                    base_url = browse.get("canonicalBaseUrl") or ""
                    channel_handle = base_url[1:] if base_url.startswith("/@") else None
    image = lv.get("contentImage")
    badges = [b.get("text") for b in find_all(image, "thumbnailBadgeViewModel") if b.get("text")]
    sources = next(find_all(image, "sources"), None) or []
    thumbnails = [{**s, "url": https(s.get("url"))} for s in sources]
    title = text(md.get("title"))
    content_id = lv.get("contentId")
    kind = lv.get("contentType")

    if kind == "LOCKUP_CONTENT_TYPE_PLAYLIST":
        return {
            "type": "playlist",
            "playlistId": content_id,
            "title": title,
            "channelId": channel_id,
            "channelTitle": channel_title,
            "videoCountText": badges[0] if badges else None,
            "url": f"https://www.youtube.com/playlist?list={content_id}",
            "thumbnails": thumbnails,
        }
    if kind != "LOCKUP_CONTENT_TYPE_VIDEO":
        return None

    view_parts: list[dict] = []
    for row in rows:
        parts = row.get("metadataParts") or []
        if parts and not dig(parts[0], "text", "commandRuns"):
            view_parts = parts
    index = dig(
        lv, "rendererContext", "commandContext", "onTap", "innertubeCommand", "watchEndpoint", "index"
    )
    return _feed_item(
        videoId=content_id,
        title=title,
        channelId=channel_id,
        channelTitle=channel_title,
        channelHandle=channel_handle,
        lengthText=next((b for b in badges if _DURATION_RE.match(b)), None),
        viewCountText=dig(view_parts, 0, "text", "content"),
        publishedTimeText=dig(view_parts, 1, "text", "content"),
        thumbnails=thumbnails,
        index=str(index) if index is not None else None,
        members_only=contains_text(lv, "Members only"),
    )


def shorts_lockup(s: dict) -> dict:
    reel = dig(s, "onTap", "innertubeCommand", "reelWatchEndpoint") or {}
    return _feed_item(
        videoId=reel.get("videoId"),
        title=dig(s, "overlayMetadata", "primaryText", "content"),
        viewCountText=dig(s, "overlayMetadata", "secondaryText", "content"),
        thumbnails=_thumbs(reel.get("thumbnail")),
    )


def playlist_video_renderer(p: dict) -> dict:
    run = (dig(p, "shortBylineText", "runs") or [{}])[0]
    browse = dig(run, "navigationEndpoint", "browseEndpoint") or {}
    base_url = browse.get("canonicalBaseUrl") or ""
    return _feed_item(
        videoId=p.get("videoId"),
        title=text(p.get("title")),
        channelId=browse.get("browseId"),
        channelTitle=run.get("text"),
        channelHandle=base_url[1:] if base_url.startswith("/@") else None,
        lengthText=text(p.get("lengthText")),
        viewCountText=text(p.get("videoInfo")),
        thumbnails=_thumbs(p.get("thumbnail")),
        index=text(p.get("index")),
    )


def feed_item(kind: str, node: dict) -> dict | None:
    """Uniform feed item for channel tabs, uploads and playlists."""
    if kind == "lockupViewModel":
        item = lockup(node)
        return item if item and "type" not in item else None
    if kind == "shortsLockupViewModel":
        return shorts_lockup(node)
    if kind == "playlistVideoRenderer":
        return playlist_video_renderer(node)
    return None


def _digits(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits or None


def playlist_info(resp: dict) -> dict | None:
    header = dig(resp, "header", "playlistHeaderRenderer")
    if header:
        return {
            "title": text(header.get("title")),
            "numVideos": _digits(text(header.get("numVideosText"))),
            "description": text(header.get("descriptionText")) or "",
            "ownerName": text(header.get("ownerText")),
            "viewCount": _digits(text(header.get("viewCountText"))),
        }
    micro = dig(resp, "microformat", "microformatDataRenderer")
    if micro:
        return {
            "title": micro.get("title"),
            "numVideos": None,
            "description": micro.get("description") or "",
            "ownerName": None,
            "viewCount": None,
        }
    return None

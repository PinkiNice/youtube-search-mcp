import json
import re
from pathlib import Path

import pytest

from yt_transcript import parsers
from yt_transcript.listings import _page, _search_item, _video_only, parse_rss, search_params

FIXTURES = Path(__file__).parent / "fixtures"
TED = "UCAuUUnT6oDeKwE6v1NGQxug"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("video", None, None, None, []), "EgIQAQ=="),
        (("channel", None, None, None, []), "EgIQAg=="),
        (("video", "views", None, None, []), "CAMSAhAB"),
        (("video", None, None, "short", []), "EgQQARgB"),
    ],
)
def test_search_params_golden(args, expected):
    assert search_params(*args) == expected


def test_search_videos():
    results, token = _page(load("search_video.json"), _search_item)
    videos = [r for r in results if r["type"] == "video"]
    assert len(videos) >= 10
    for v in videos:
        assert re.fullmatch(r"[A-Za-z0-9_-]{11}", v["videoId"])
        assert v["channelId"].startswith("UC")
    assert token


def test_search_channels():
    results, _ = _page(load("search_channel.json"), _search_item)
    first = results[0]
    assert first["type"] == "channel"
    assert first["channelId"] == TED
    assert first["handle"] == "@TED"
    assert "subscribers" in first["subscriberCount"]


def test_search_playlists():
    results, _ = _page(load("search_playlist.json"), _search_item)
    assert results
    for p in results:
        assert p["type"] == "playlist"
        assert p["playlistId"]
        assert p["videoCountText"].endswith("videos")


def test_uploads_feed_and_playlist_info():
    resp = load("uploads_ted.json")
    results, token = _page(resp, parsers.feed_item)
    assert 90 <= len(results) <= 100
    assert results[0]["index"] == "0"
    assert re.fullmatch(r"\d+(:\d{2})+", results[0]["lengthText"])
    assert results[0]["channelId"] == TED
    assert token
    info = parsers.playlist_info(resp)
    assert info["title"] == "Uploads from TED"
    assert info["numVideos"].isdigit()


def test_shorts_tab():
    results, _ = _page(load("tab_shorts_ted.json"), parsers.feed_item)
    assert results
    assert all(r["videoId"] and r["title"] for r in results)


def test_channel_search_returns_only_videos():
    results, token = _page(load("channel_search_ted.json"), _video_only)
    assert results
    assert {r["type"] for r in results} == {"video"}
    assert token


def test_rss():
    feed = parse_rss((FIXTURES / "rss_ted.xml").read_text(), TED)
    assert feed["result_count"] == 15
    assert feed["channel"]["channelId"] == TED
    assert all(r["videoId"] and r["published"] and r["viewCount"] is not None for r in feed["results"])

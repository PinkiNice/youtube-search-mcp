import json
from collections import Counter
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from yt_transcript.config import Settings
from yt_transcript.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
PLAYER_OK = (FIXTURES / "player_ok_dqw.json").read_text()
PLAYER_AGE = (FIXTURES / "player_age.json").read_text()
PLAYER_BOT = (FIXTURES / "player_bot.json").read_text()
TIMEDTEXT = (FIXTURES / "timedtext_dqw.json3").read_text()

URL = "/api/v2/youtube/transcript?video_url=dQw4w9WgXcQ"


def make_client(tmp_path, players: dict[str, str], timedtext: str = TIMEDTEXT, **settings):
    calls: Counter = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/youtubei/v1/player":
            client = json.loads(request.content)["context"]["client"]["clientName"]
            calls[client] += 1
            return httpx.Response(200, text=players.get(client, players.get("*", PLAYER_BOT)))
        if request.url.path == "/api/timedtext":
            calls["timedtext"] += 1
            return httpx.Response(200, text=timedtext)
        return httpx.Response(404)

    app = create_app(
        Settings(
            cache_path=str(tmp_path / "c.sqlite3"),
            yt_min_interval_ms=0,
            ytdlp_fallback=False,
            **settings,
        ),
        transport=httpx.MockTransport(handler),
    )
    return TestClient(app), calls


def test_bot_check_rotates_client_benches_it_and_caches(tmp_path):
    client, calls = make_client(tmp_path, {"ANDROID": PLAYER_BOT, "IOS": PLAYER_OK})
    with client:
        r = client.get(URL)
        assert r.status_code == 200
        body = r.json()
        assert body["language"] == "en"
        assert body["length_seconds"] == 213
        assert r.headers["X-Cache-Status"] == "MISS"
        assert r.headers["X-Served-By"] == "innertube:IOS"

        cooldowns = client.get("/health").json()["cooldowns"]
        assert [c["client"] for c in cooldowns] == ["ANDROID"]

        before = sum(calls.values())
        again = client.get(URL)
        assert again.headers["X-Cache-Status"] == "HIT"
        assert sum(calls.values()) == before


def test_age_restricted(tmp_path):
    client, _ = make_client(tmp_path, {"*": PLAYER_AGE})
    with client:
        r = client.get(URL)
    assert r.status_code == 404
    assert r.json()["code"] == "age_restricted"


def test_empty_caption_body_is_temporarily_blocked(tmp_path):
    client, _ = make_client(tmp_path, {"*": PLAYER_OK}, timedtext="")
    with client:
        r = client.get(URL)
    assert r.status_code == 408
    assert r.json()["code"] == "temporarily_blocked"
    assert r.headers["Retry-After"] == "5"


def test_unknown_language_lists_available(tmp_path):
    client, _ = make_client(tmp_path, {"*": PLAYER_OK})
    with client:
        r = client.get(URL + "&language=xx")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "no_transcript_for_requested_languages"
    codes = {lang["code"] for lang in body["available_languages"]}
    assert {"en", "asr-en", "de", "ja"} <= codes


def test_api_key_required(tmp_path):
    client, _ = make_client(tmp_path, {"*": PLAYER_OK}, api_key="k")
    with client:
        r = client.get(URL)
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"

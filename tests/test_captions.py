import json
from pathlib import Path

import pytest

from yt_transcript.captions import (
    Track,
    available_languages,
    length_text,
    parse_json3,
    parse_language_param,
    resolve_track,
    tracks_from_player,
)
from yt_transcript.errors import ApiError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def dqw_tracks() -> list[Track]:
    return tracks_from_player(json.loads((FIXTURES / "player_ok_dqw.json").read_text()))


def test_default_prefers_manual_english(dqw_tracks):
    assert resolve_track(dqw_tracks, None, True).vss_id == ".en"


def test_regional_track_matched_by_base_code(dqw_tracks):
    track = resolve_track(dqw_tracks, ["de"], True)
    assert track.vss_id == ".de-DE"
    assert track.code == "de"


def test_asr_selects_auto_track(dqw_tracks):
    track = resolve_track(dqw_tracks, ["asr"], True)
    assert track.vss_id == "a.en"
    assert track.code == "asr-en"


def test_first_available_requested_language_wins(dqw_tracks):
    assert resolve_track(dqw_tracks, ["fr", "ja"], True).code == "ja"


def test_asr_only_language_respects_fallback_flag():
    tracks = [Track("hi", True, "Hindi (auto-generated)", "a.hi")]
    assert resolve_track(tracks, ["hi"], True).code == "asr-hi"
    assert resolve_track(tracks, ["hi"], False) is None
    codes = {(lang["code"], lang["kind"]) for lang in available_languages(tracks)}
    assert codes == {("hi", "auto"), ("asr-hi", "auto")}


def test_parse_json3_fixture():
    segments = parse_json3((FIXTURES / "timedtext_dqw.json3").read_text())
    assert segments[0] == {"text": "[♪♪♪]", "start": 1.36, "duration": 1.68}
    assert all(s["text"] for s in segments)


def test_length_text():
    assert length_text(213) == "3:33"
    assert length_text(3765) == "1:02:45"
    assert length_text(None) is None


def test_too_many_language_codes_rejected():
    with pytest.raises(ApiError) as exc:
        parse_language_param(",".join(["en"] * 11))
    assert exc.value.status == 422

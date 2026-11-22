import pytest

from yt_transcript.errors import ApiError
from yt_transcript.ids import parse_video_id


@pytest.mark.parametrize(
    "value",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",
        " dQw4w9WgXcQ ",
    ],
)
def test_parse_video_id(value):
    assert parse_video_id(value) == "dQw4w9WgXcQ"


def test_invalid_video_id():
    with pytest.raises(ApiError) as exc:
        parse_video_id("notavideo")
    assert (exc.value.status, exc.value.code) == (422, "invalid_video_url")

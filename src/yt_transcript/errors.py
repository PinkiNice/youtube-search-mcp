class ApiError(Exception):
    """Error returned to API clients as `{"detail", "code", ...extra}`."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        extra: dict | None = None,
        headers: dict | None = None,
    ):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra
        self.headers = headers

    def body(self) -> dict:
        return {"detail": self.detail, "code": self.code, **(self.extra or {})}


class RetryableUpstream(Exception):
    """Internal: this attempt was blocked/failed transiently; try another client/proxy.

    reason: bot_check | rate_limited | empty_body | network | http_5xx | reload_required
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def invalid_video(value: str) -> ApiError:
    return ApiError(422, "invalid_video_url", "Invalid YouTube URL or video ID")


def validation(detail: str) -> ApiError:
    return ApiError(422, "validation_error", detail)


def video_unavailable(vid: str) -> ApiError:
    return ApiError(404, "video_unavailable", f"Video {vid} not found or unavailable")


def age_restricted(vid: str) -> ApiError:
    return ApiError(
        404,
        "age_restricted",
        f"Video {vid} is age-restricted: YouTube only shows it to signed-in adults, "
        "so no transcript is available",
    )


def live_not_started(vid: str) -> ApiError:
    return ApiError(
        404,
        "live_not_started",
        f"Video {vid} is a live stream that has not started yet, try again after it has aired",
    )


def no_captions(vid: str) -> ApiError:
    return ApiError(404, "no_captions", f"No transcript available for video {vid}")


def no_requested_languages(codes: list[str], available: list[dict]) -> ApiError:
    return ApiError(
        404,
        "no_transcript_for_requested_languages",
        f"No transcript available for the requested languages: {', '.join(codes)}",
        extra={"available_languages": available},
    )


def temporarily_blocked() -> ApiError:
    return ApiError(
        408,
        "temporarily_blocked",
        "YouTube temporarily blocked the request (bot check or rate limit); retry in a few seconds",
        headers={"Retry-After": "5"},
    )


def upstream_error(msg: str) -> ApiError:
    return ApiError(503, "upstream_error", msg)


def channel_not_found(x: str) -> ApiError:
    return ApiError(404, "channel_not_found", f"Channel {x} not found")


def playlist_not_found(x: str) -> ApiError:
    return ApiError(404, "playlist_not_found", f"Playlist {x} not found")


def invalid_continuation() -> ApiError:
    return ApiError(
        400, "invalid_continuation", "Invalid request argument (e.g. a malformed continuation token)"
    )


def unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "Invalid or missing API key")

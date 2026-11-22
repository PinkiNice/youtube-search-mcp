import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_cache_path() -> str:
    # Per-user location so the cache works no matter which directory the server is launched from.
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base) / "yt-transcript" / "cache.sqlite3")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    api_key: str = ""
    yt_proxies: str = ""
    yt_cookies_file: str = ""
    yt_player_clients: str = "ANDROID,IOS,ANDROID_VR"
    yt_max_concurrency: int = 4
    yt_min_interval_ms: int = 300
    yt_client_cooldown_s: int = 600
    yt_request_timeout_s: float = 15.0
    ytdlp_fallback: bool = True
    cache_enabled: bool = True
    cache_path: str = Field(default_factory=_default_cache_path)
    log_level: str = "INFO"

    @property
    def proxy_list(self) -> list[str | None]:
        proxies = [p.strip() for p in self.yt_proxies.split(",") if p.strip()]
        return list(proxies) if proxies else [None]

    @property
    def player_client_list(self) -> list[str]:
        return [c.strip().upper() for c in self.yt_player_clients.split(",") if c.strip()]

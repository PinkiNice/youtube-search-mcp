import asyncio
import json
import os
import sqlite3
import threading
import time
from typing import Any

TTL_INFO = 6 * 3600
TTL_TRANSCRIPT = 7 * 86400
TTL_SEARCH = 600
TTL_CHANNEL = 300
TTL_PLAYLIST = 1800
TTL_RESOLVE = 7 * 86400
TTL_RSS = 300


class Cache:
    """JSON key/value cache with per-entry expiry in a single SQLite file."""

    def __init__(self, path: str, enabled: bool):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if not enabled:
            return
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL)"
            )
            self._db.execute("DELETE FROM cache WHERE expires_at <= ?", (time.time(),))
            self._db.commit()

    def _get(self, key: str) -> Any:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM cache WHERE key = ? AND expires_at > ?", (key, time.time())
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _set(self, key: str, value: Any, ttl_s: float) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, payload, time.time() + ttl_s),
            )
            self._db.commit()

    async def get(self, key: str) -> Any:
        if self._db is None:
            return None
        return await asyncio.to_thread(self._get, key)

    async def set(self, key: str, value: Any, ttl_s: float) -> None:
        if self._db is None:
            return
        await asyncio.to_thread(self._set, key, value, ttl_s)

    def close(self) -> None:
        if self._db is not None:
            with self._lock:
                self._db.close()
            self._db = None

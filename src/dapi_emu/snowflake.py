"""Snowflake ID generator (Discord-compatible 64bit)."""
from __future__ import annotations

import threading
import time

DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01 UTC

_lock = threading.Lock()
_last_ms = 0
_increment = 0
_worker_id = 0
_process_id = 0


def generate() -> str:
    """Return a new snowflake as a string (required by Discord API contract)."""
    global _last_ms, _increment
    with _lock:
        now = int(time.time() * 1000)
        if now == _last_ms:
            _increment = (_increment + 1) & 0xFFF
            if _increment == 0:
                while now <= _last_ms:
                    now = int(time.time() * 1000)
        else:
            _increment = 0
        _last_ms = now
        value = ((now - DISCORD_EPOCH_MS) << 22) | (_worker_id << 17) | (_process_id << 12) | _increment
    return str(value)


def timestamp_of(snowflake: str | int) -> int:
    """Extract unix-ms timestamp from a snowflake."""
    return (int(snowflake) >> 22) + DISCORD_EPOCH_MS

"""Rate limit: always emit X-RateLimit-* headers; optionally enforce."""
from __future__ import annotations

import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

ENFORCE = os.environ.get("DAPI_ENFORCE_RATELIMIT", "0") == "1"

# Per-bucket token window (shared default, overrideable by route).
DEFAULT_CAPACITY = 50
DEFAULT_WINDOW_SEC = 2.0
GLOBAL_CAPACITY = 50
GLOBAL_WINDOW_SEC = 1.0


def _derive_bucket(method: str, path: str) -> str:
    """Group related routes into the same bucket.

    Snowflake IDs collapse to '{id}'. Any non-ASCII segment (e.g. a URL-decoded
    emoji in a reaction path) collapses to '{x}' — header values are latin-1
    only, so leaking raw unicode into X-RateLimit-Bucket would 500.
    """
    parts: list[str] = []
    for p in path.split("/"):
        if p.isdigit() and len(p) >= 15:
            parts.append("{id}")
        elif p.isascii():
            parts.append(p)
        else:
            parts.append("{x}")
    return f"{method} {'/'.join(parts)}"


class _Bucket:
    __slots__ = ("count", "reset_at", "capacity", "window")

    def __init__(self, capacity: int, window: float) -> None:
        self.count = 0
        self.reset_at = 0.0
        self.capacity = capacity
        self.window = window

    def take(self) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds_if_blocked)."""
        now = time.time()
        if self.reset_at < now:
            self.count = 0
            self.reset_at = now + self.window
        if self.count >= self.capacity:
            return False, max(0.0, self.reset_at - now)
        self.count += 1
        return True, 0.0


_buckets: dict[str, _Bucket] = defaultdict(
    lambda: _Bucket(DEFAULT_CAPACITY, DEFAULT_WINDOW_SEC)
)
_global_bucket = _Bucket(GLOBAL_CAPACITY, GLOBAL_WINDOW_SEC)


def _headers_for(bucket_name: str, bucket: _Bucket) -> dict[str, str]:
    now = time.time()
    remaining = max(0, bucket.capacity - bucket.count)
    reset_after = max(0.0, bucket.reset_at - now) if bucket.reset_at > now else bucket.window
    return {
        "X-RateLimit-Limit": str(bucket.capacity),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": f"{bucket.reset_at:.3f}",
        "X-RateLimit-Reset-After": f"{reset_after:.3f}",
        "X-RateLimit-Bucket": bucket_name,
        "X-RateLimit-Scope": "user",
    }


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Only rate limit Discord-like REST paths; leave admin/panel alone.
        path = request.url.path
        is_api = path.startswith("/api/")

        bucket_name = _derive_bucket(request.method, path) if is_api else "static"
        bucket = _buckets[bucket_name] if is_api else _Bucket(1000, 1.0)

        if ENFORCE and is_api:
            # Global first, then route-specific.
            ok_g, retry_g = _global_bucket.take()
            if not ok_g:
                return JSONResponse(
                    {"message": "You are being rate limited.", "retry_after": retry_g, "global": True, "code": 0},
                    status_code=429,
                    headers={
                        "Retry-After": f"{retry_g:.3f}",
                        "X-RateLimit-Global": "true",
                        "X-RateLimit-Scope": "global",
                    },
                )
            ok, retry = bucket.take()
            if not ok:
                return JSONResponse(
                    {"message": "You are being rate limited.", "retry_after": retry, "global": False, "code": 0},
                    status_code=429,
                    headers={
                        "Retry-After": f"{retry:.3f}",
                        **_headers_for(bucket_name, bucket),
                    },
                )

        response = await call_next(request)
        for k, v in _headers_for(bucket_name, bucket).items():
            response.headers.setdefault(k, v)
        return response

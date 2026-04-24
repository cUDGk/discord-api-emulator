"""Rate limit headers middleware. Not enforcing limits (emulator) but emitting
X-RateLimit-* headers so client libraries don't complain."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", "50")
        response.headers.setdefault("X-RateLimit-Remaining", "49")
        response.headers.setdefault("X-RateLimit-Reset-After", "1.0")
        response.headers.setdefault("X-RateLimit-Bucket", "emulator")
        response.headers.setdefault("X-RateLimit-Scope", "user")
        return response

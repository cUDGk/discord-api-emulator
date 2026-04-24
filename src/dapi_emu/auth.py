"""Bot token / Bearer token parsing and auth dependency."""
from __future__ import annotations

from fastapi import Header, HTTPException

from .state import WORLD, User


def parse_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail={"code": 0, "message": "401: Unauthorized"})
    if authorization.startswith("Bot "):
        return authorization[4:].strip()
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def require_bot(authorization: str | None = Header(default=None)) -> User:
    token = parse_token(authorization)
    user_id = WORLD.bot_tokens.get(token)
    if not user_id or user_id not in WORLD.users:
        raise HTTPException(status_code=401, detail={"code": 0, "message": "401: Unauthorized"})
    return WORLD.users[user_id]

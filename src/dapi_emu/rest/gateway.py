from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import config
from ..auth import require_bot

router = APIRouter()


@router.get("/gateway")
async def get_gateway() -> dict:
    return {"url": config.GATEWAY_WS_URL}


@router.get("/gateway/bot")
async def get_gateway_bot(_bot=Depends(require_bot)) -> dict:
    return {
        "url": config.GATEWAY_WS_URL,
        "shards": 1,
        "session_start_limit": {
            "total": 1000,
            "remaining": 999,
            "reset_after": 86400000,
            "max_concurrency": 1,
        },
    }

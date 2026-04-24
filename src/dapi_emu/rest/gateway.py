from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from .. import config
from ..auth import require_bot
from ..state import WORLD

router = APIRouter()


def _recommended_shards() -> int:
    """Discord recommends 1 shard per ~1000 guilds; allow env override for tests."""
    override = os.environ.get("DAPI_SHARD_COUNT")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return max(1, len(WORLD.guilds) // 1000) or 1


@router.get("/gateway")
async def get_gateway() -> dict:
    return {"url": config.GATEWAY_WS_URL}


@router.get("/gateway/bot")
async def get_gateway_bot(_bot=Depends(require_bot)) -> dict:
    return {
        "url": config.GATEWAY_WS_URL,
        "shards": _recommended_shards(),
        "session_start_limit": {
            "total": 1000,
            "remaining": 999,
            "reset_after": 86400000,
            "max_concurrency": 1,
        },
    }

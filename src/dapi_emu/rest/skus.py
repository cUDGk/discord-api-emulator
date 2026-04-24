from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_application(application_id: str):
    app = WORLD.applications.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})
    return app


def _require_sku(application_id: str, sku_id: str) -> dict:
    sku = WORLD.skus.get(sku_id)
    if not sku or sku.get("application_id") != application_id:
        raise HTTPException(status_code=404, detail={"code": 10069, "message": "Unknown SKU"})
    return sku


@router.get("/applications/{application_id}/skus")
async def list_skus(application_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_application(application_id)
    return [s for s in WORLD.skus.values() if s.get("application_id") == application_id]


@router.get("/applications/{application_id}/skus/{sku_id}/subscriptions")
async def list_sku_subscriptions(
    application_id: str,
    sku_id: str,
    before: str | None = None,
    after: str | None = None,
    limit: int = 50,
    user_id: str | None = None,
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_application(application_id)
    _require_sku(application_id, sku_id)
    return []


@router.get("/applications/{application_id}/skus/{sku_id}/subscriptions/{subscription_id}")
async def get_sku_subscription(
    application_id: str,
    sku_id: str,
    subscription_id: str,
    _bot: User = Depends(require_bot),
) -> dict:
    _require_application(application_id)
    _require_sku(application_id, sku_id)
    raise HTTPException(status_code=404, detail={"code": 10070, "message": "Unknown Subscription"})

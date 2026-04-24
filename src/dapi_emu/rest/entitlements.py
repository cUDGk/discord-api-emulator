from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


def _require_application(application_id: str):
    app = WORLD.applications.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})
    return app


def _require_entitlement(application_id: str, entitlement_id: str) -> dict:
    ent = WORLD.entitlements.get(entitlement_id)
    if not ent or ent.get("application_id") != application_id:
        raise HTTPException(status_code=404, detail={"code": 10068, "message": "Unknown Entitlement"})
    return ent


@router.get("/applications/{application_id}/entitlements")
async def list_entitlements(
    application_id: str,
    user_id: str | None = None,
    sku_ids: str | None = None,
    before: str | None = None,
    after: str | None = None,
    limit: int = 100,
    guild_id: str | None = None,
    exclude_ended: bool = False,
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_application(application_id)
    sku_filter: set[str] | None = None
    if sku_ids:
        sku_filter = {s for s in sku_ids.split(",") if s}
    result: list[dict] = []
    for ent in WORLD.entitlements.values():
        if ent.get("application_id") != application_id:
            continue
        if user_id and ent.get("user_id") != user_id:
            continue
        if guild_id and ent.get("guild_id") != guild_id:
            continue
        if sku_filter and ent.get("sku_id") not in sku_filter:
            continue
        if exclude_ended and ent.get("ends_at"):
            continue
        if before and int(ent["id"]) >= int(before):
            continue
        if after and int(ent["id"]) <= int(after):
            continue
        result.append(ent)
    return result[: max(1, min(limit, 100))]


@router.get("/applications/{application_id}/entitlements/{entitlement_id}")
async def get_entitlement(
    application_id: str,
    entitlement_id: str,
    _bot: User = Depends(require_bot),
) -> dict:
    _require_application(application_id)
    return _require_entitlement(application_id, entitlement_id)


@router.post("/applications/{application_id}/entitlements", status_code=200)
async def create_test_entitlement(
    application_id: str,
    body: dict,
    _bot: User = Depends(require_bot),
) -> dict:
    _require_application(application_id)
    sku_id = body.get("sku_id")
    owner_id = body.get("owner_id")
    owner_type = body.get("owner_type")
    if not sku_id or not owner_id or owner_type not in (1, 2):
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
    eid = new_snowflake()
    ent: dict = {
        "id": eid,
        "sku_id": sku_id,
        "application_id": application_id,
        "type": 1,
        "deleted": False,
        "starts_at": datetime.now(timezone.utc).isoformat(),
        "ends_at": None,
        "consumed": False,
    }
    # owner_type: 1 = GUILD_SUBSCRIPTION, 2 = USER_SUBSCRIPTION
    if owner_type == 1:
        ent["guild_id"] = owner_id
    else:
        ent["user_id"] = owner_id
    WORLD.entitlements[eid] = ent
    WORLD.bus.publish("ENTITLEMENT_CREATE", ent)
    return ent


@router.delete("/applications/{application_id}/entitlements/{entitlement_id}", status_code=204)
async def delete_test_entitlement(
    application_id: str,
    entitlement_id: str,
    _bot: User = Depends(require_bot),
):
    _require_application(application_id)
    ent = _require_entitlement(application_id, entitlement_id)
    ent["deleted"] = True
    WORLD.entitlements.pop(entitlement_id, None)
    WORLD.bus.publish("ENTITLEMENT_DELETE", ent)


@router.post("/applications/{application_id}/entitlements/{entitlement_id}/consume", status_code=204)
async def consume_entitlement(
    application_id: str,
    entitlement_id: str,
    _bot: User = Depends(require_bot),
):
    _require_application(application_id)
    ent = _require_entitlement(application_id, entitlement_id)
    ent["consumed"] = True
    WORLD.bus.publish("ENTITLEMENT_UPDATE", ent)

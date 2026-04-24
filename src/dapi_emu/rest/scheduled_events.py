from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import audit
from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


def _require_guild(guild_id: str):
    g = WORLD.guilds.get(guild_id)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


def _require_event(guild_id: str, event_id: str) -> dict[str, Any]:
    ev = WORLD.scheduled_events.get(event_id)
    if not ev or ev.get("guild_id") != guild_id:
        raise HTTPException(status_code=404, detail={"code": 10070, "message": "Unknown Guild Scheduled Event"})
    return ev


def _with_counts(ev: dict[str, Any], with_user_count: bool) -> dict[str, Any]:
    if not with_user_count:
        out = dict(ev)
        out.pop("user_count", None)
        return out
    return dict(ev)


@router.get("/guilds/{guild_id}/scheduled-events")
async def list_scheduled_events(
    guild_id: str,
    with_user_count: bool = False,
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_guild(guild_id)
    ids = WORLD.guild_scheduled_events.get(guild_id, [])
    return [_with_counts(WORLD.scheduled_events[i], with_user_count) for i in ids if i in WORLD.scheduled_events]


@router.post("/guilds/{guild_id}/scheduled-events", status_code=200)
async def create_scheduled_event(guild_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    name = body.get("name")
    scheduled_start_time = body.get("scheduled_start_time")
    entity_type = int(body.get("entity_type", 3))
    if not name or not scheduled_start_time:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "name and scheduled_start_time are required"})
    event_id = new_snowflake()
    creator = WORLD.users[bot.id].to_dict() if bot.id in WORLD.users else {"id": bot.id}
    ev: dict[str, Any] = {
        "id": event_id,
        "guild_id": guild_id,
        "channel_id": body.get("channel_id"),
        "creator_id": bot.id,
        "creator": creator,
        "name": name,
        "description": body.get("description"),
        "scheduled_start_time": scheduled_start_time,
        "scheduled_end_time": body.get("scheduled_end_time"),
        "privacy_level": int(body.get("privacy_level", 2)),
        "status": int(body.get("status", 1)),
        "entity_type": entity_type,
        "entity_id": body.get("entity_id"),
        "entity_metadata": body.get("entity_metadata"),
        "user_count": 0,
        "image": body.get("image"),
    }
    WORLD.scheduled_events[event_id] = ev
    WORLD.guild_scheduled_events.setdefault(guild_id, []).append(event_id)
    WORLD.bus.publish("GUILD_SCHEDULED_EVENT_CREATE", ev)
    audit.log(  # GUILD_SCHEDULED_EVENT_CREATE
        guild_id, bot.id, event_id, 110,
        changes=[{"key": "name", "new_value": name}],
    )
    return ev


@router.get("/guilds/{guild_id}/scheduled-events/{event_id}")
async def get_scheduled_event(
    guild_id: str,
    event_id: str,
    with_user_count: bool = False,
    _bot: User = Depends(require_bot),
) -> dict:
    ev = _require_event(guild_id, event_id)
    return _with_counts(ev, with_user_count)


@router.patch("/guilds/{guild_id}/scheduled-events/{event_id}")
async def edit_scheduled_event(guild_id: str, event_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    ev = _require_event(guild_id, event_id)
    changes: list[dict] = []
    for field in (
        "channel_id",
        "name",
        "description",
        "scheduled_start_time",
        "scheduled_end_time",
        "privacy_level",
        "status",
        "entity_type",
        "entity_metadata",
        "image",
    ):
        if field in body:
            old = ev.get(field)
            new = body[field]
            if old != new:
                changes.append({"key": field, "old_value": old, "new_value": new})
            ev[field] = new
    WORLD.bus.publish("GUILD_SCHEDULED_EVENT_UPDATE", ev)
    audit.log(guild_id, bot.id, event_id, 111, changes=changes)  # GUILD_SCHEDULED_EVENT_UPDATE
    return ev


@router.delete("/guilds/{guild_id}/scheduled-events/{event_id}", status_code=204)
async def delete_scheduled_event(guild_id: str, event_id: str, bot: User = Depends(require_bot)):
    ev = _require_event(guild_id, event_id)
    name = ev.get("name")
    WORLD.scheduled_events.pop(event_id, None)
    if event_id in WORLD.guild_scheduled_events.get(guild_id, []):
        WORLD.guild_scheduled_events[guild_id].remove(event_id)
    WORLD.bus.publish("GUILD_SCHEDULED_EVENT_DELETE", ev)
    audit.log(  # GUILD_SCHEDULED_EVENT_DELETE
        guild_id, bot.id, event_id, 112,
        changes=[{"key": "name", "old_value": name}],
    )


@router.get("/guilds/{guild_id}/scheduled-events/{event_id}/users")
async def list_scheduled_event_users(
    guild_id: str,
    event_id: str,
    limit: int = Query(100, ge=1, le=100),
    with_member: bool = False,
    before: str | None = None,
    after: str | None = None,
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_event(guild_id, event_id)
    return []

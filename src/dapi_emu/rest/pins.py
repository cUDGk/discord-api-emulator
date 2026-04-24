"""Pins, bulk-delete, and channel permission overwrites."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_channel(channel_id: str) -> Any:
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return ch


def _require_message(channel_id: str, message_id: str) -> Any:
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    return m


# --- Pins -----------------------------------------------------------------

@router.get("/channels/{channel_id}/pins")
async def list_pins(channel_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_channel(channel_id)
    ids = WORLD.pinned_messages.get(channel_id, [])
    msgs = [WORLD.messages[i] for i in ids if i in WORLD.messages]
    msgs.sort(key=lambda m: int(m.id), reverse=True)
    return [m.to_dict(WORLD.users) for m in msgs]


@router.put("/channels/{channel_id}/pins/{message_id}", status_code=204)
async def pin_message(
    channel_id: str, message_id: str, _bot: User = Depends(require_bot)
) -> None:
    ch = _require_channel(channel_id)
    m = _require_message(channel_id, message_id)
    m.pinned = True
    pins = WORLD.pinned_messages.setdefault(channel_id, [])
    if message_id not in pins:
        pins.append(message_id)
    WORLD.bus.publish("CHANNEL_PINS_UPDATE", {
        "guild_id": ch.guild_id,
        "channel_id": channel_id,
        "last_pin_timestamp": datetime.now(timezone.utc).isoformat(),
    })


@router.delete("/channels/{channel_id}/pins/{message_id}", status_code=204)
async def unpin_message(
    channel_id: str, message_id: str, _bot: User = Depends(require_bot)
) -> None:
    ch = _require_channel(channel_id)
    m = _require_message(channel_id, message_id)
    m.pinned = False
    pins = WORLD.pinned_messages.get(channel_id, [])
    if message_id in pins:
        pins.remove(message_id)
    WORLD.bus.publish("CHANNEL_PINS_UPDATE", {
        "guild_id": ch.guild_id,
        "channel_id": channel_id,
        "last_pin_timestamp": datetime.now(timezone.utc).isoformat(),
    })


# --- Bulk delete ----------------------------------------------------------

@router.post("/channels/{channel_id}/messages/bulk-delete", status_code=204)
async def bulk_delete_messages(
    channel_id: str, body: dict, _bot: User = Depends(require_bot)
) -> None:
    ch = _require_channel(channel_id)
    ids = body.get("messages") or []
    if not isinstance(ids, list) or not (2 <= len(ids) <= 100):
        raise HTTPException(
            status_code=400,
            detail={"code": 50016, "message": "Provided too few or too many messages to delete."},
        )
    deleted: list[str] = []
    for mid in ids:
        mid = str(mid)
        m = WORLD.messages.get(mid)
        if not m or m.channel_id != channel_id:
            continue
        WORLD.messages.pop(mid, None)
        if mid in WORLD.channel_messages.get(channel_id, []):
            WORLD.channel_messages[channel_id].remove(mid)
        deleted.append(mid)
    WORLD.bus.publish("MESSAGE_DELETE_BULK", {
        "ids": deleted,
        "channel_id": channel_id,
        "guild_id": ch.guild_id,
    })


# --- Permission overwrites ------------------------------------------------

@router.put("/channels/{channel_id}/permissions/{overwrite_id}", status_code=204)
async def upsert_permission_overwrite(
    channel_id: str, overwrite_id: str, body: dict,
    _bot: User = Depends(require_bot),
) -> None:
    ch = _require_channel(channel_id)
    overwrite = {
        "id": overwrite_id,
        "type": int(body.get("type", 0)),  # 0=role, 1=member
        "allow": str(body.get("allow", "0")),
        "deny": str(body.get("deny", "0")),
    }
    arr = WORLD.channel_overwrites.setdefault(channel_id, [])
    for i, existing in enumerate(arr):
        if existing.get("id") == overwrite_id:
            arr[i] = overwrite
            break
    else:
        arr.append(overwrite)
    WORLD.bus.publish("CHANNEL_UPDATE", ch.to_dict(WORLD.users))


@router.delete("/channels/{channel_id}/permissions/{overwrite_id}", status_code=204)
async def delete_permission_overwrite(
    channel_id: str, overwrite_id: str, _bot: User = Depends(require_bot)
) -> None:
    ch = _require_channel(channel_id)
    arr = WORLD.channel_overwrites.get(channel_id, [])
    WORLD.channel_overwrites[channel_id] = [o for o in arr if o.get("id") != overwrite_id]
    WORLD.bus.publish("CHANNEL_UPDATE", ch.to_dict(WORLD.users))

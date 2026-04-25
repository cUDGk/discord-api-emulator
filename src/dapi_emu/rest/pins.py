"""Pins, bulk-delete, and channel permission overwrites."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import audit
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
    channel_id: str, message_id: str, bot: User = Depends(require_bot)
) -> None:
    from .. import system_messages
    ch = _require_channel(channel_id)
    m = _require_message(channel_id, message_id)
    was_pinned = m.pinned
    m.pinned = True
    pins = WORLD.pinned_messages.setdefault(channel_id, [])
    if message_id not in pins:
        pins.append(message_id)
    WORLD.bus.publish("CHANNEL_PINS_UPDATE", {
        "guild_id": ch.guild_id,
        "channel_id": channel_id,
        "last_pin_timestamp": datetime.now(timezone.utc).isoformat(),
    })
    audit.log(  # MESSAGE_PIN
        ch.guild_id, bot.id, m.author_id, 74,
        options={"channel_id": channel_id, "message_id": message_id},
    )
    if not was_pinned:
        system_messages.message_pinned(channel_id, bot.id, message_id)


@router.delete("/channels/{channel_id}/pins/{message_id}", status_code=204)
async def unpin_message(
    channel_id: str, message_id: str, bot: User = Depends(require_bot)
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
    audit.log(  # MESSAGE_UNPIN
        ch.guild_id, bot.id, m.author_id, 75,
        options={"channel_id": channel_id, "message_id": message_id},
    )


# --- Bulk delete ----------------------------------------------------------

@router.post("/channels/{channel_id}/messages/bulk-delete", status_code=204)
async def bulk_delete_messages(
    channel_id: str, body: dict, bot: User = Depends(require_bot)
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
    if deleted:
        audit.log(  # MESSAGE_BULK_DELETE
            ch.guild_id, bot.id, channel_id, 73,
            options={"count": str(len(deleted))},
        )


# --- Permission overwrites ------------------------------------------------

@router.put("/channels/{channel_id}/permissions/{overwrite_id}", status_code=204)
async def upsert_permission_overwrite(
    channel_id: str, overwrite_id: str, body: dict,
    bot: User = Depends(require_bot),
) -> None:
    ch = _require_channel(channel_id)
    ow_type = int(body.get("type", 0))  # 0=role, 1=member
    overwrite = {
        "id": overwrite_id,
        "type": ow_type,
        "allow": str(body.get("allow", "0")),
        "deny": str(body.get("deny", "0")),
    }
    arr = WORLD.channel_overwrites.setdefault(channel_id, [])
    is_update = False
    old: dict[str, Any] | None = None
    for i, existing in enumerate(arr):
        if existing.get("id") == overwrite_id:
            old = existing
            arr[i] = overwrite
            is_update = True
            break
    else:
        arr.append(overwrite)
    WORLD.bus.publish("CHANNEL_UPDATE", ch.to_dict(WORLD.users))
    options = {
        "id": overwrite_id,
        "type": str(ow_type),
        "role_name": "" if ow_type != 0 else (
            WORLD.roles[overwrite_id].name if overwrite_id in WORLD.roles else ""
        ),
    }
    if is_update:
        changes: list[dict] = []
        for k in ("allow", "deny"):
            o_v = (old or {}).get(k)
            n_v = overwrite[k]
            if o_v != n_v:
                changes.append({"key": k, "old_value": o_v, "new_value": n_v})
        audit.log(  # CHANNEL_OVERWRITE_UPDATE
            ch.guild_id, bot.id, channel_id, 14,
            changes=changes, options=options,
        )
    else:
        audit.log(  # CHANNEL_OVERWRITE_CREATE
            ch.guild_id, bot.id, channel_id, 13,
            changes=[
                {"key": "id", "new_value": overwrite_id},
                {"key": "type", "new_value": ow_type},
                {"key": "allow", "new_value": overwrite["allow"]},
                {"key": "deny", "new_value": overwrite["deny"]},
            ],
            options=options,
        )


@router.delete("/channels/{channel_id}/permissions/{overwrite_id}", status_code=204)
async def delete_permission_overwrite(
    channel_id: str, overwrite_id: str, bot: User = Depends(require_bot)
) -> None:
    ch = _require_channel(channel_id)
    arr = WORLD.channel_overwrites.get(channel_id, [])
    target = next((o for o in arr if o.get("id") == overwrite_id), None)
    WORLD.channel_overwrites[channel_id] = [o for o in arr if o.get("id") != overwrite_id]
    WORLD.bus.publish("CHANNEL_UPDATE", ch.to_dict(WORLD.users))
    ow_type = (target or {}).get("type", 0)
    audit.log(  # CHANNEL_OVERWRITE_DELETE
        ch.guild_id, bot.id, channel_id, 15,
        options={
            "id": overwrite_id,
            "type": str(ow_type),
            "role_name": "" if ow_type != 0 else (
                WORLD.roles[overwrite_id].name if overwrite_id in WORLD.roles else ""
            ),
        },
    )

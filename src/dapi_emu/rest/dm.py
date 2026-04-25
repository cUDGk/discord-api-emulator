"""DM / Group DM channel endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _find_existing_dm(user_a: str, user_b: str) -> str | None:
    """Return the channel_id of an existing 1:1 DM between two users, if any."""
    pair = {user_a, user_b}
    for ch in WORLD.channels.values():
        if ch.type == 1 and set(ch.recipients) == pair:
            return ch.id
    return None


@router.post("/users/@me/channels")
async def create_dm(body: dict, bot: User = Depends(require_bot)) -> dict:
    recipient_id = body.get("recipient_id")
    recipients = body.get("recipients") or []

    if recipient_id:
        other = str(recipient_id)
        if other not in WORLD.users:
            raise HTTPException(status_code=404, detail={"code": 10013, "message": "Unknown User"})
        existing = _find_existing_dm(bot.id, other)
        if existing:
            return WORLD.channels[existing].to_dict(WORLD.users)
        ch = WORLD.create_channel(name="", type=1, guild_id=None)
        ch.recipients = [bot.id, other]
        return ch.to_dict(WORLD.users)

    # Group DM — requires 2+ recipients (plus bot = >=3 participants)
    rids = [str(r) for r in recipients]
    unknown = [r for r in rids if r not in WORLD.users]
    if unknown:
        raise HTTPException(status_code=404, detail={"code": 10013, "message": "Unknown User"})
    ch = WORLD.create_channel(name="", type=3, guild_id=None)
    ch.recipients = list(dict.fromkeys([bot.id, *rids]))
    return ch.to_dict(WORLD.users)


@router.get("/users/@me/channels")
async def list_my_dms(bot: User = Depends(require_bot)) -> list[dict]:
    out: list[dict] = []
    for ch in WORLD.channels.values():
        if ch.type in (1, 3) and bot.id in ch.recipients:
            out.append(ch.to_dict(WORLD.users))
    return out


@router.post("/channels/{channel_id}/recipients/{user_id}", status_code=204)
async def group_dm_add_recipient(
    channel_id: str, user_id: str, bot: User = Depends(require_bot)
) -> None:
    from .. import system_messages
    ch = WORLD.channels.get(channel_id)
    if not ch or ch.type != 3:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    if user_id not in WORLD.users:
        raise HTTPException(status_code=404, detail={"code": 10013, "message": "Unknown User"})
    was_present = user_id in ch.recipients
    if not was_present:
        ch.recipients.append(user_id)
    WORLD.bus.publish("CHANNEL_RECIPIENT_ADD", {
        "channel_id": channel_id,
        "user": WORLD.users[user_id].to_dict(),
    })
    if not was_present:
        system_messages.recipient_added(channel_id, bot.id, user_id)


@router.delete("/channels/{channel_id}/recipients/{user_id}", status_code=204)
async def group_dm_remove_recipient(
    channel_id: str, user_id: str, bot: User = Depends(require_bot)
) -> None:
    from .. import system_messages
    ch = WORLD.channels.get(channel_id)
    if not ch or ch.type != 3:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    was_present = user_id in ch.recipients
    if was_present:
        ch.recipients.remove(user_id)
    user = WORLD.users.get(user_id)
    WORLD.bus.publish("CHANNEL_RECIPIENT_REMOVE", {
        "channel_id": channel_id,
        "user": user.to_dict() if user else {"id": user_id},
    })
    if was_present:
        system_messages.recipient_removed(channel_id, bot.id, user_id)

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


class CreateMessageBody(BaseModel):
    content: str | None = None
    tts: bool | None = False
    embeds: list[dict[str, Any]] | None = None
    embed: dict[str, Any] | None = None
    allowed_mentions: dict[str, Any] | None = None
    message_reference: dict[str, Any] | None = None
    components: list[dict[str, Any]] | None = None
    sticker_ids: list[str] | None = None
    flags: int | None = None
    nonce: Any | None = None
    enforce_nonce: bool | None = None


class EditMessageBody(BaseModel):
    content: str | None = None
    embeds: list[dict[str, Any]] | None = None
    flags: int | None = None
    allowed_mentions: dict[str, Any] | None = None
    components: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] | None = None


def _require_channel(channel_id: str):
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return ch


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str, _bot: User = Depends(require_bot)) -> dict:
    ch = _require_channel(channel_id)
    return ch.to_dict(WORLD.users)


@router.patch("/channels/{channel_id}")
async def edit_channel(channel_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    ch = _require_channel(channel_id)
    for field in ("name", "topic", "nsfw", "position", "parent_id", "rate_limit_per_user"):
        if field in body:
            setattr(ch, field, body[field])
    WORLD.bus.publish("CHANNEL_UPDATE", ch.to_dict(WORLD.users))
    return ch.to_dict(WORLD.users)


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, _bot: User = Depends(require_bot)) -> dict:
    ch = _require_channel(channel_id)
    d = ch.to_dict(WORLD.users)
    # remove from guild
    if ch.guild_id and ch.guild_id in WORLD.guilds:
        g = WORLD.guilds[ch.guild_id]
        if channel_id in g.channel_ids:
            g.channel_ids.remove(channel_id)
    WORLD.channels.pop(channel_id, None)
    WORLD.channel_messages.pop(channel_id, None)
    WORLD.bus.publish("CHANNEL_DELETE", d)
    return d


@router.get("/channels/{channel_id}/messages")
async def list_messages(
    channel_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: str | None = None,
    after: str | None = None,
    around: str | None = None,
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_channel(channel_id)
    ids = WORLD.channel_messages.get(channel_id, [])
    msgs = [WORLD.messages[i] for i in ids if i in WORLD.messages]
    # newest first
    msgs.sort(key=lambda m: int(m.id), reverse=True)
    if before:
        msgs = [m for m in msgs if int(m.id) < int(before)]
    elif after:
        msgs = [m for m in msgs if int(m.id) > int(after)]
        msgs.reverse()  # oldest first for after
    elif around:
        pivot = int(around)
        msgs.sort(key=lambda m: abs(int(m.id) - pivot))
    return [m.to_dict(WORLD.users) for m in msgs[:limit]]


@router.get("/channels/{channel_id}/messages/{message_id}")
async def get_message(channel_id: str, message_id: str, _bot: User = Depends(require_bot)) -> dict:
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    return m.to_dict(WORLD.users)


@router.post("/channels/{channel_id}/messages", status_code=200)
async def create_message(
    channel_id: str,
    body: CreateMessageBody,
    bot: User = Depends(require_bot),
) -> dict:
    _require_channel(channel_id)
    embeds = body.embeds or ([body.embed] if body.embed else [])
    msg = WORLD.create_message(
        channel_id=channel_id,
        author_id=bot.id,
        content=body.content or "",
        embeds=embeds,
        components=body.components or [],
        flags=body.flags or 0,
        tts=bool(body.tts),
    )
    payload = msg.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_CREATE", payload)
    return payload


@router.patch("/channels/{channel_id}/messages/{message_id}")
async def edit_message(
    channel_id: str,
    message_id: str,
    body: EditMessageBody,
    bot: User = Depends(require_bot),
) -> dict:
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    if body.content is not None:
        m.content = body.content
    if body.embeds is not None:
        m.embeds = body.embeds
    if body.components is not None:
        m.components = body.components
    if body.flags is not None:
        m.flags = body.flags
    m.edited_timestamp = datetime.now(timezone.utc).isoformat()
    payload = m.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_UPDATE", payload)
    return payload


@router.delete("/channels/{channel_id}/messages/{message_id}", status_code=204)
async def delete_message(channel_id: str, message_id: str, _bot: User = Depends(require_bot)):
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    WORLD.messages.pop(message_id, None)
    if message_id in WORLD.channel_messages.get(channel_id, []):
        WORLD.channel_messages[channel_id].remove(message_id)
    WORLD.bus.publish("MESSAGE_DELETE", {
        "id": message_id, "channel_id": channel_id, "guild_id": m.guild_id,
    })


@router.post("/channels/{channel_id}/typing", status_code=204)
async def trigger_typing(channel_id: str, bot: User = Depends(require_bot)):
    ch = _require_channel(channel_id)
    WORLD.bus.publish("TYPING_START", {
        "channel_id": channel_id,
        "guild_id": ch.guild_id,
        "user_id": bot.id,
        "timestamp": int(datetime.now(timezone.utc).timestamp()),
        "member": WORLD.members[(bot.id, ch.guild_id)].to_dict(WORLD.users) if ch.guild_id and (bot.id, ch.guild_id) in WORLD.members else None,
    })


# Reactions ----------------------------------------------------------------

def _find_reaction(msg, emoji_key: str) -> dict | None:
    for r in msg.reactions:
        if r["emoji"].get("name") == emoji_key or r["emoji"].get("id") == emoji_key:
            return r
    return None


@router.put("/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me", status_code=204)
async def add_reaction(channel_id: str, message_id: str, emoji: str, bot: User = Depends(require_bot)):
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    # emoji may be name, or name:id for custom
    name, _, eid = emoji.partition(":")
    emoji_obj = {"name": name, "id": eid or None, "animated": False}
    r = _find_reaction(m, name if not eid else eid)
    if r:
        r["count"] += 1
        r["me"] = True
    else:
        m.reactions.append({"count": 1, "me": True, "emoji": emoji_obj, "burst_count": 0,
                             "burst_me": False, "burst_colors": [], "count_details": {"burst": 0, "normal": 1}})
    WORLD.bus.publish("MESSAGE_REACTION_ADD", {
        "user_id": bot.id, "channel_id": channel_id, "message_id": message_id,
        "guild_id": m.guild_id, "emoji": emoji_obj,
    })


@router.delete("/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me", status_code=204)
async def remove_reaction(channel_id: str, message_id: str, emoji: str, bot: User = Depends(require_bot)):
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    name, _, eid = emoji.partition(":")
    r = _find_reaction(m, name if not eid else eid)
    if r:
        r["count"] = max(0, r["count"] - 1)
        r["me"] = False
        if r["count"] == 0:
            m.reactions.remove(r)
    WORLD.bus.publish("MESSAGE_REACTION_REMOVE", {
        "user_id": bot.id, "channel_id": channel_id, "message_id": message_id,
        "guild_id": m.guild_id, "emoji": {"name": name, "id": eid or None, "animated": False},
    })

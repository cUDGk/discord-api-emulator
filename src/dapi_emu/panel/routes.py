"""Test control panel — browser UI + admin API for injecting events.

Not part of the Discord-compatible surface. Lives under /panel and /admin.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ..state import WORLD

router = APIRouter()

TEMPLATE = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


@router.get("/panel", response_class=HTMLResponse)
async def panel_index() -> HTMLResponse:
    return HTMLResponse(TEMPLATE)


# --- Snapshot ------------------------------------------------------------

@router.get("/admin/state")
async def get_state() -> dict:
    return {
        "users": [u.to_dict(private=True) for u in WORLD.users.values()],
        "guilds": [
            {
                "id": g.id,
                "name": g.name,
                "owner_id": g.owner_id,
                "channels": [WORLD.channels[c].to_dict() for c in g.channel_ids if c in WORLD.channels],
                "roles": [WORLD.roles[r].to_dict() for r in g.role_ids if r in WORLD.roles],
                "members": [
                    WORLD.members[(uid, g.id)].to_dict(WORLD.users)
                    for uid in g.member_ids if (uid, g.id) in WORLD.members
                ],
            }
            for g in WORLD.guilds.values()
        ],
        "applications": [
            {"id": a.id, "name": a.name, "bot_id": a.bot_id}
            for a in WORLD.applications.values()
        ],
        "bot_tokens": [
            {"token": tok, "bot_id": bid, "username": WORLD.users[bid].username if bid in WORLD.users else None}
            for tok, bid in WORLD.bot_tokens.items()
        ],
    }


# --- User / Bot CRUD ------------------------------------------------------

@router.post("/admin/users")
async def create_user(body: dict) -> dict:
    username = body.get("username")
    if not username:
        raise HTTPException(400, "username required")
    bot = bool(body.get("bot", False))
    u = WORLD.create_user(username, bot=bot)
    result = {"user": u.to_dict(private=True)}
    if bot:
        app, token = WORLD.register_bot(u, token=body.get("token"))
        result["application_id"] = app.id
        result["token"] = token
    return result


@router.delete("/admin/users/{user_id}", status_code=204)
async def delete_user(user_id: str) -> None:
    WORLD.users.pop(user_id, None)
    # remove from all guilds
    for g in WORLD.guilds.values():
        if user_id in g.member_ids:
            g.member_ids.remove(user_id)
        WORLD.members.pop((user_id, g.id), None)
    # remove bot tokens
    for tok, bid in list(WORLD.bot_tokens.items()):
        if bid == user_id:
            WORLD.bot_tokens.pop(tok, None)


# --- Guild / Channel / Role CRUD -----------------------------------------

@router.post("/admin/guilds")
async def admin_create_guild(body: dict) -> dict:
    name = body.get("name", "Test Guild")
    owner_id = body.get("owner_id")
    if not owner_id or owner_id not in WORLD.users:
        raise HTTPException(400, "owner_id required and must exist")
    g = WORLD.create_guild(name, owner_id)
    # Create a default #general channel
    WORLD.create_channel("general", type=0, guild_id=g.id)
    return g.to_dict(WORLD, full=True)


@router.post("/admin/guilds/{guild_id}/members")
async def admin_add_member(guild_id: str, body: dict) -> dict:
    user_id = body.get("user_id")
    if not user_id or user_id not in WORLD.users:
        raise HTTPException(400, "user_id required")
    if guild_id not in WORLD.guilds:
        raise HTTPException(404, "guild not found")
    m = WORLD.add_member(user_id, guild_id, nick=body.get("nick"), roles=body.get("roles", []))
    payload = m.to_dict(WORLD.users) | {"guild_id": guild_id}
    WORLD.bus.publish("GUILD_MEMBER_ADD", payload)
    return payload


@router.post("/admin/guilds/{guild_id}/channels")
async def admin_create_channel(guild_id: str, body: dict) -> dict:
    if guild_id not in WORLD.guilds:
        raise HTTPException(404, "guild not found")
    ch = WORLD.create_channel(
        name=body.get("name", "channel"),
        type=int(body.get("type", 0)),
        guild_id=guild_id,
        parent_id=body.get("parent_id"),
    )
    payload = ch.to_dict()
    WORLD.bus.publish("CHANNEL_CREATE", payload)
    return payload


@router.post("/admin/guilds/{guild_id}/roles")
async def admin_create_role(guild_id: str, body: dict) -> dict:
    if guild_id not in WORLD.guilds:
        raise HTTPException(404, "guild not found")
    role = WORLD.create_role(
        guild_id=guild_id,
        name=body.get("name", "role"),
        color=int(body.get("color", 0)),
        hoist=bool(body.get("hoist", False)),
        permissions=str(body.get("permissions", "0")),
        mentionable=bool(body.get("mentionable", False)),
    )
    WORLD.bus.publish("GUILD_ROLE_CREATE", {"guild_id": guild_id, "role": role.to_dict()})
    return role.to_dict()


# --- Event injection ------------------------------------------------------

@router.post("/admin/send-message")
async def admin_send_message(body: dict) -> dict:
    """Simulate a user sending a message in a channel."""
    author_id = body.get("author_id")
    channel_id = body.get("channel_id")
    content = body.get("content", "")
    if not author_id or author_id not in WORLD.users:
        raise HTTPException(400, "author_id required")
    if not channel_id or channel_id not in WORLD.channels:
        raise HTTPException(400, "channel_id required")
    msg = WORLD.create_message(channel_id, author_id, content)
    payload = msg.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_CREATE", payload)
    return payload


@router.post("/admin/dispatch")
async def admin_raw_dispatch(body: dict) -> dict:
    """Raw dispatch any event. Advanced users only."""
    event = body.get("event")
    data = body.get("data", {})
    if not event:
        raise HTTPException(400, "event required")
    WORLD.bus.publish(event, data)
    return {"dispatched": event}


@router.post("/admin/reset")
async def admin_reset() -> dict:
    """Wipe the world. Useful between tests."""
    WORLD.users.clear()
    WORLD.guilds.clear()
    WORLD.channels.clear()
    WORLD.messages.clear()
    WORLD.channel_messages.clear()
    WORLD.roles.clear()
    WORLD.members.clear()
    WORLD.applications.clear()
    WORLD.bot_tokens.clear()
    WORLD.commands.clear()
    WORLD.guild_commands.clear()
    WORLD.global_commands.clear()
    return {"ok": True}

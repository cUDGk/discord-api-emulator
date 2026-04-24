"""Test control panel — browser UI + admin API for injecting events.

Not part of the Discord-compatible surface. Lives under /panel and /admin.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from .. import persistence
from ..state import WORLD

router = APIRouter()

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _load(name: str) -> str:
    path = _TEMPLATE_DIR / name
    if not path.exists():
        return f"<h1>{name} not found</h1>"
    return path.read_text(encoding="utf-8")


@router.get("/panel", response_class=HTMLResponse)
async def panel_index() -> HTMLResponse:
    return HTMLResponse(_load("index.html"))


@router.get("/client", response_class=HTMLResponse)
async def client_index() -> HTMLResponse:
    return HTMLResponse(_load("client.html"))


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


@router.get("/admin/channels/{channel_id}/messages")
async def admin_list_messages(channel_id: str, limit: int = 50) -> list[dict]:
    ids = WORLD.channel_messages.get(channel_id, [])
    msgs = [WORLD.messages[i] for i in ids if i in WORLD.messages]
    msgs.sort(key=lambda m: int(m.id))  # oldest first (UI friendly)
    return [m.to_dict(WORLD.users) for m in msgs[-limit:]]


@router.delete("/admin/messages/{message_id}", status_code=204)
async def admin_delete_message(message_id: str) -> None:
    m = WORLD.messages.get(message_id)
    if not m:
        return
    WORLD.messages.pop(message_id, None)
    if message_id in WORLD.channel_messages.get(m.channel_id, []):
        WORLD.channel_messages[m.channel_id].remove(message_id)
    WORLD.bus.publish("MESSAGE_DELETE", {
        "id": message_id, "channel_id": m.channel_id, "guild_id": m.guild_id,
    })


@router.patch("/admin/messages/{message_id}")
async def admin_edit_message(message_id: str, body: dict) -> dict:
    from datetime import datetime, timezone
    m = WORLD.messages.get(message_id)
    if not m:
        raise HTTPException(404, "message not found")
    if "content" in body:
        m.content = body["content"]
    m.edited_timestamp = datetime.now(timezone.utc).isoformat()
    payload = m.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_UPDATE", payload)
    return payload


@router.post("/admin/messages/{message_id}/reactions")
async def admin_add_reaction(message_id: str, body: dict) -> dict:
    user_id = body.get("user_id")
    emoji = body.get("emoji", "")
    if not user_id or user_id not in WORLD.users:
        raise HTTPException(400, "user_id required")
    m = WORLD.messages.get(message_id)
    if not m:
        raise HTTPException(404, "message not found")
    name, _, eid = emoji.partition(":")
    emoji_obj = {"name": name, "id": eid or None, "animated": False}
    found = next((r for r in m.reactions if r["emoji"].get("name") == name and r["emoji"].get("id") == (eid or None)), None)
    if found:
        found["count"] += 1
    else:
        m.reactions.append({
            "count": 1, "me": False, "emoji": emoji_obj,
            "burst_count": 0, "burst_me": False, "burst_colors": [],
            "count_details": {"burst": 0, "normal": 1},
        })
    WORLD.bus.publish("MESSAGE_REACTION_ADD", {
        "user_id": user_id, "channel_id": m.channel_id, "message_id": message_id,
        "guild_id": m.guild_id, "emoji": emoji_obj,
    })
    return {"ok": True}


@router.delete("/admin/messages/{message_id}/reactions")
async def admin_remove_reaction(message_id: str, user_id: str, emoji: str) -> None:
    m = WORLD.messages.get(message_id)
    if not m:
        raise HTTPException(404, "message not found")
    name, _, eid = emoji.partition(":")
    for r in list(m.reactions):
        if r["emoji"].get("name") == name and r["emoji"].get("id") == (eid or None):
            r["count"] = max(0, r["count"] - 1)
            if r["count"] == 0:
                m.reactions.remove(r)
            break
    WORLD.bus.publish("MESSAGE_REACTION_REMOVE", {
        "user_id": user_id, "channel_id": m.channel_id, "message_id": message_id,
        "guild_id": m.guild_id, "emoji": {"name": name, "id": eid or None, "animated": False},
    })


@router.put("/admin/guilds/{guild_id}/members/{user_id}/roles/{role_id}", status_code=204)
async def admin_add_member_role(guild_id: str, user_id: str, role_id: str) -> None:
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        raise HTTPException(404, "member not found")
    if role_id not in m.roles:
        m.roles.append(role_id)
    WORLD.bus.publish("GUILD_MEMBER_UPDATE", m.to_dict(WORLD.users) | {"guild_id": guild_id})


@router.delete("/admin/guilds/{guild_id}/members/{user_id}/roles/{role_id}", status_code=204)
async def admin_remove_member_role(guild_id: str, user_id: str, role_id: str) -> None:
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        raise HTTPException(404, "member not found")
    if role_id in m.roles:
        m.roles.remove(role_id)
    WORLD.bus.publish("GUILD_MEMBER_UPDATE", m.to_dict(WORLD.users) | {"guild_id": guild_id})


@router.patch("/admin/guilds/{guild_id}/members/{user_id}")
async def admin_edit_member(guild_id: str, user_id: str, body: dict) -> dict:
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        raise HTTPException(404, "member not found")
    if "nick" in body:
        m.nick = body["nick"]
    if "roles" in body:
        m.roles = list(body["roles"])
    if "deaf" in body:
        m.deaf = bool(body["deaf"])
    if "mute" in body:
        m.mute = bool(body["mute"])
    payload = m.to_dict(WORLD.users) | {"guild_id": guild_id}
    WORLD.bus.publish("GUILD_MEMBER_UPDATE", payload)
    return payload


@router.delete("/admin/guilds/{guild_id}/members/{user_id}", status_code=204)
async def admin_kick_member(guild_id: str, user_id: str) -> None:
    g = WORLD.guilds.get(guild_id)
    if g and user_id in g.member_ids:
        g.member_ids.remove(user_id)
    WORLD.members.pop((user_id, guild_id), None)
    u = WORLD.users.get(user_id)
    WORLD.bus.publish("GUILD_MEMBER_REMOVE", {
        "guild_id": guild_id,
        "user": u.to_dict() if u else {"id": user_id},
    })


@router.delete("/admin/guilds/{guild_id}/roles/{role_id}", status_code=204)
async def admin_delete_role(guild_id: str, role_id: str) -> None:
    WORLD.roles.pop(role_id, None)
    g = WORLD.guilds.get(guild_id)
    if g and role_id in g.role_ids:
        g.role_ids.remove(role_id)
    # strip from all members
    for (uid, gid), m in WORLD.members.items():
        if gid == guild_id and role_id in m.roles:
            m.roles.remove(role_id)
    WORLD.bus.publish("GUILD_ROLE_DELETE", {"guild_id": guild_id, "role_id": role_id})


@router.patch("/admin/guilds/{guild_id}/roles/{role_id}")
async def admin_edit_role(guild_id: str, role_id: str, body: dict) -> dict:
    role = WORLD.roles.get(role_id)
    if not role or role.guild_id != guild_id:
        raise HTTPException(404, "role not found")
    for f in ("name", "hoist", "mentionable", "position"):
        if f in body:
            setattr(role, f, body[f])
    if "color" in body:
        role.color = int(body["color"])
    if "permissions" in body:
        role.permissions = str(body["permissions"])
    WORLD.bus.publish("GUILD_ROLE_UPDATE", {"guild_id": guild_id, "role": role.to_dict()})
    return role.to_dict()


@router.delete("/admin/guilds/{guild_id}/channels/{channel_id}", status_code=204)
async def admin_delete_channel(guild_id: str, channel_id: str) -> None:
    ch = WORLD.channels.get(channel_id)
    if not ch:
        return
    d = ch.to_dict()
    g = WORLD.guilds.get(guild_id)
    if g and channel_id in g.channel_ids:
        g.channel_ids.remove(channel_id)
    WORLD.channels.pop(channel_id, None)
    WORLD.channel_messages.pop(channel_id, None)
    WORLD.bus.publish("CHANNEL_DELETE", d)


@router.patch("/admin/guilds/{guild_id}/channels/{channel_id}")
async def admin_edit_channel(guild_id: str, channel_id: str, body: dict) -> dict:
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    for f in ("name", "topic", "position", "parent_id", "rate_limit_per_user"):
        if f in body:
            setattr(ch, f, body[f])
    if "nsfw" in body:
        ch.nsfw = bool(body["nsfw"])
    payload = ch.to_dict()
    WORLD.bus.publish("CHANNEL_UPDATE", payload)
    return payload


@router.delete("/admin/guilds/{guild_id}", status_code=204)
async def admin_delete_guild(guild_id: str) -> None:
    g = WORLD.guilds.pop(guild_id, None)
    if not g:
        return
    for cid in list(g.channel_ids):
        WORLD.channels.pop(cid, None)
        WORLD.channel_messages.pop(cid, None)
    for rid in list(g.role_ids):
        WORLD.roles.pop(rid, None)
    for uid in list(g.member_ids):
        WORLD.members.pop((uid, guild_id), None)
    WORLD.bus.publish("GUILD_DELETE", {"id": guild_id, "unavailable": False})


@router.put("/admin/polls/{message_id}")
async def admin_set_poll(message_id: str, body: dict) -> dict:
    """Attach poll state to an existing message. Used by tests."""
    if message_id not in WORLD.messages:
        raise HTTPException(404, "message not found")
    WORLD.polls[message_id] = body
    return {"ok": True, "message_id": message_id}


@router.put("/admin/teams/{team_id}")
async def admin_set_team(team_id: str, body: dict) -> dict:
    """Upsert a team. Used by tests."""
    WORLD.teams[team_id] = {"id": team_id, **body}
    return WORLD.teams[team_id]


@router.post("/admin/interaction-tokens")
async def admin_register_interaction_token(body: dict) -> dict:
    """Pre-register an interaction token. Used by slash/button tests."""
    tok = body.get("token")
    if not tok:
        raise HTTPException(400, "token required")
    WORLD.interaction_tokens[tok] = {
        "interaction_id": body.get("interaction_id", tok),
        "interaction_type": body.get("interaction_type", 2),
        "application_id": body.get("application_id"),
        "channel_id": body.get("channel_id"),
        "guild_id": body.get("guild_id"),
        "user_id": body.get("user_id"),
    }
    return {"ok": True, "token": tok}


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
    if persistence.is_enabled():
        persistence.wipe_db(os.environ["DAPI_DB_PATH"])
    return {"ok": True}

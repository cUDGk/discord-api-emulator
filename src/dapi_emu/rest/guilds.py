from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_guild(gid: str):
    g = WORLD.guilds.get(gid)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


@router.get("/guilds/{guild_id}")
async def get_guild(guild_id: str, with_counts: bool = False, _bot: User = Depends(require_bot)) -> dict:
    g = _require_guild(guild_id)
    d = g.to_dict(WORLD)
    if with_counts:
        d["approximate_member_count"] = len(g.member_ids)
        d["approximate_presence_count"] = 0
    return d


@router.patch("/guilds/{guild_id}")
async def edit_guild(guild_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    g = _require_guild(guild_id)
    for field in ("name", "description", "icon"):
        if field in body:
            setattr(g, field, body[field])
    payload = g.to_dict(WORLD)
    WORLD.bus.publish("GUILD_UPDATE", payload)
    return payload


@router.get("/guilds/{guild_id}/channels")
async def list_channels(guild_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    g = _require_guild(guild_id)
    return [WORLD.channels[c].to_dict() for c in g.channel_ids if c in WORLD.channels]


@router.post("/guilds/{guild_id}/channels", status_code=201)
async def create_channel(guild_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    ch = WORLD.create_channel(
        name=body.get("name", "channel"),
        type=body.get("type", 0),
        guild_id=guild_id,
        parent_id=body.get("parent_id"),
    )
    if "topic" in body:
        ch.topic = body["topic"]
    if "nsfw" in body:
        ch.nsfw = bool(body["nsfw"])
    if "position" in body:
        ch.position = int(body["position"])
    payload = ch.to_dict()
    WORLD.bus.publish("CHANNEL_CREATE", payload)
    return payload


# Members ------------------------------------------------------------------

@router.get("/guilds/{guild_id}/members")
async def list_members(
    guild_id: str,
    limit: int = Query(1, ge=1, le=1000),
    after: str = "0",
    _bot: User = Depends(require_bot),
) -> list[dict]:
    g = _require_guild(guild_id)
    members = []
    for uid in g.member_ids:
        if int(uid) > int(after) and (uid, guild_id) in WORLD.members:
            members.append(WORLD.members[(uid, guild_id)].to_dict(WORLD.users))
    members.sort(key=lambda m: int(m["user"]["id"]))
    return members[:limit]


@router.get("/guilds/{guild_id}/members/{user_id}")
async def get_member(guild_id: str, user_id: str, _bot: User = Depends(require_bot)) -> dict:
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10007, "message": "Unknown Member"})
    return m.to_dict(WORLD.users)


@router.patch("/guilds/{guild_id}/members/{user_id}")
async def edit_member(guild_id: str, user_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10007, "message": "Unknown Member"})
    if "nick" in body:
        m.nick = body["nick"]
    if "roles" in body:
        m.roles = body["roles"]
    if "deaf" in body:
        m.deaf = bool(body["deaf"])
    if "mute" in body:
        m.mute = bool(body["mute"])
    payload = m.to_dict(WORLD.users) | {"guild_id": guild_id}
    WORLD.bus.publish("GUILD_MEMBER_UPDATE", payload)
    return m.to_dict(WORLD.users)


@router.put("/guilds/{guild_id}/members/{user_id}/roles/{role_id}", status_code=204)
async def add_member_role(guild_id: str, user_id: str, role_id: str, _bot: User = Depends(require_bot)):
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10007, "message": "Unknown Member"})
    if role_id not in m.roles:
        m.roles.append(role_id)
    WORLD.bus.publish("GUILD_MEMBER_UPDATE", m.to_dict(WORLD.users) | {"guild_id": guild_id})


@router.delete("/guilds/{guild_id}/members/{user_id}/roles/{role_id}", status_code=204)
async def remove_member_role(guild_id: str, user_id: str, role_id: str, _bot: User = Depends(require_bot)):
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10007, "message": "Unknown Member"})
    if role_id in m.roles:
        m.roles.remove(role_id)
    WORLD.bus.publish("GUILD_MEMBER_UPDATE", m.to_dict(WORLD.users) | {"guild_id": guild_id})


@router.delete("/guilds/{guild_id}/members/{user_id}", status_code=204)
async def kick_member(guild_id: str, user_id: str, _bot: User = Depends(require_bot)):
    g = WORLD.guilds.get(guild_id)
    if g and user_id in g.member_ids:
        g.member_ids.remove(user_id)
    WORLD.members.pop((user_id, guild_id), None)
    user = WORLD.users.get(user_id)
    WORLD.bus.publish("GUILD_MEMBER_REMOVE", {
        "guild_id": guild_id,
        "user": user.to_dict() if user else {"id": user_id},
    })


# Roles --------------------------------------------------------------------

@router.get("/guilds/{guild_id}/roles")
async def list_roles(guild_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    g = _require_guild(guild_id)
    return [WORLD.roles[r].to_dict() for r in g.role_ids if r in WORLD.roles]


@router.post("/guilds/{guild_id}/roles", status_code=200)
async def create_role(guild_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    role = WORLD.create_role(
        guild_id=guild_id,
        name=body.get("name", "new role"),
        color=int(body.get("color", 0)),
        hoist=bool(body.get("hoist", False)),
        permissions=str(body.get("permissions", "0")),
        mentionable=bool(body.get("mentionable", False)),
    )
    WORLD.bus.publish("GUILD_ROLE_CREATE", {"guild_id": guild_id, "role": role.to_dict()})
    return role.to_dict()


@router.patch("/guilds/{guild_id}/roles/{role_id}")
async def edit_role(guild_id: str, role_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    role = WORLD.roles.get(role_id)
    if not role or role.guild_id != guild_id:
        raise HTTPException(status_code=404, detail={"code": 10011, "message": "Unknown Role"})
    for f in ("name", "color", "hoist", "permissions", "mentionable", "position"):
        if f in body:
            setattr(role, f, body[f] if f != "permissions" else str(body[f]))
    WORLD.bus.publish("GUILD_ROLE_UPDATE", {"guild_id": guild_id, "role": role.to_dict()})
    return role.to_dict()


@router.delete("/guilds/{guild_id}/roles/{role_id}", status_code=204)
async def delete_role(guild_id: str, role_id: str, _bot: User = Depends(require_bot)):
    role = WORLD.roles.get(role_id)
    if not role or role.guild_id != guild_id:
        raise HTTPException(status_code=404, detail={"code": 10011, "message": "Unknown Role"})
    WORLD.roles.pop(role_id, None)
    g = WORLD.guilds.get(guild_id)
    if g and role_id in g.role_ids:
        g.role_ids.remove(role_id)
    WORLD.bus.publish("GUILD_ROLE_DELETE", {"guild_id": guild_id, "role_id": role_id})

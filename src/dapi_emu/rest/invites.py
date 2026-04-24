from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_channel(channel_id: str):
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return ch


def _require_guild(guild_id: str):
    g = WORLD.guilds.get(guild_id)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


def _invite_payload(inv: dict, *, with_counts: bool = False, with_expiration: bool = False) -> dict:
    out = dict(inv)
    if with_counts:
        gid = (inv.get("guild") or {}).get("id")
        guild = WORLD.guilds.get(gid) if gid else None
        out["approximate_member_count"] = len(guild.member_ids) if guild else 0
        out["approximate_presence_count"] = 0
    if with_expiration and "expires_at" not in out:
        out["expires_at"] = inv.get("expires_at")
    return out


@router.get("/invites/{code}")
async def get_invite(
    code: str,
    with_counts: bool = False,
    with_expiration: bool = False,
    guild_scheduled_event_id: str | None = None,
    _bot: User = Depends(require_bot),
) -> dict:
    inv = WORLD.invites.get(code)
    if not inv:
        raise HTTPException(status_code=404, detail={"code": 10006, "message": "Unknown Invite"})
    payload = _invite_payload(inv, with_counts=with_counts, with_expiration=with_expiration)
    if guild_scheduled_event_id:
        payload["guild_scheduled_event"] = WORLD.scheduled_events.get(guild_scheduled_event_id)
    return payload


@router.delete("/invites/{code}")
async def delete_invite(code: str, _bot: User = Depends(require_bot)) -> dict:
    inv = WORLD.invites.pop(code, None)
    if not inv:
        raise HTTPException(status_code=404, detail={"code": 10006, "message": "Unknown Invite"})
    gid = (inv.get("guild") or {}).get("id")
    if gid and code in WORLD.guild_invites.get(gid, []):
        WORLD.guild_invites[gid].remove(code)
    cid = (inv.get("channel") or {}).get("id")
    WORLD.bus.publish("INVITE_DELETE", {
        "channel_id": cid,
        "guild_id": gid,
        "code": code,
    })
    return inv


@router.get("/channels/{channel_id}/invites")
async def list_channel_invites(channel_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_channel(channel_id)
    return [
        inv for inv in WORLD.invites.values()
        if (inv.get("channel") or {}).get("id") == channel_id
    ]


@router.post("/channels/{channel_id}/invites", status_code=200)
async def create_channel_invite(channel_id: str, body: dict | None = None, bot: User = Depends(require_bot)) -> dict:
    ch = _require_channel(channel_id)
    body = body or {}
    code = secrets.token_urlsafe(6)
    # collision guard
    while code in WORLD.invites:
        code = secrets.token_urlsafe(6)

    max_age = int(body.get("max_age", 86400))
    now = datetime.now(timezone.utc)
    expires_at = None
    if max_age > 0:
        expires_at = (now + timedelta(seconds=max_age)).isoformat()

    guild_dict = None
    if ch.guild_id and ch.guild_id in WORLD.guilds:
        g = WORLD.guilds[ch.guild_id]
        guild_dict = {
            "id": g.id,
            "name": g.name,
            "icon": g.icon,
            "description": g.description,
            "features": g.features,
        }

    inv = {
        "code": code,
        "guild": guild_dict,
        "channel": {
            "id": ch.id,
            "name": ch.name,
            "type": ch.type,
        },
        "inviter": bot.to_dict(),
        "target_type": body.get("target_type"),
        "target_user": None,
        "target_application": None,
        "uses": 0,
        "max_uses": int(body.get("max_uses", 0)),
        "max_age": max_age,
        "temporary": bool(body.get("temporary", False)),
        "created_at": now.isoformat(),
        "expires_at": expires_at,
    }

    target_user_id = body.get("target_user_id")
    if target_user_id and target_user_id in WORLD.users:
        inv["target_user"] = WORLD.users[target_user_id].to_dict()
    target_application_id = body.get("target_application_id")
    if target_application_id and target_application_id in WORLD.applications:
        inv["target_application"] = WORLD.applications[target_application_id].to_dict(WORLD.users)

    WORLD.invites[code] = inv
    if ch.guild_id:
        WORLD.guild_invites.setdefault(ch.guild_id, []).append(code)

    WORLD.bus.publish("INVITE_CREATE", {
        "channel_id": ch.id,
        "guild_id": ch.guild_id,
        "code": code,
        "created_at": inv["created_at"],
        "expires_at": inv["expires_at"],
        "inviter": inv["inviter"],
        "max_age": inv["max_age"],
        "max_uses": inv["max_uses"],
        "temporary": inv["temporary"],
        "target_type": inv["target_type"],
        "target_user": inv["target_user"],
        "target_application": inv["target_application"],
        "uses": 0,
    })
    return inv


@router.get("/guilds/{guild_id}/invites")
async def list_guild_invites(guild_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_guild(guild_id)
    codes = WORLD.guild_invites.get(guild_id, [])
    return [WORLD.invites[c] for c in codes if c in WORLD.invites]


@router.get("/guilds/{guild_id}/vanity-url")
async def get_vanity_url(guild_id: str, _bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    return {"code": None, "uses": 0}

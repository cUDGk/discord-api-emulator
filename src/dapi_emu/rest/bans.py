from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import audit
from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_guild(guild_id: str):
    g = WORLD.guilds.get(guild_id)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


def _ban_dict(user_id: str, reason: str | None) -> dict:
    user = WORLD.users.get(user_id)
    return {
        "user": user.to_dict() if user else {"id": user_id},
        "reason": reason,
    }


def _remove_member(guild_id: str, user_id: str) -> None:
    g = WORLD.guilds.get(guild_id)
    if g and user_id in g.member_ids:
        g.member_ids.remove(user_id)
    WORLD.members.pop((user_id, guild_id), None)


@router.get("/guilds/{guild_id}/bans")
async def list_bans(
    guild_id: str,
    limit: int = Query(1000, ge=1, le=1000),
    before: str | None = None,
    after: str | None = None,
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_guild(guild_id)
    entries = [
        (uid, ban) for (gid, uid), ban in WORLD.bans.items() if gid == guild_id
    ]
    entries.sort(key=lambda t: int(t[0]))
    if after:
        entries = [e for e in entries if int(e[0]) > int(after)]
    if before:
        entries = [e for e in entries if int(e[0]) < int(before)]
        entries.reverse()
    return [ban for _uid, ban in entries[:limit]]


@router.get("/guilds/{guild_id}/bans/{user_id}")
async def get_ban(guild_id: str, user_id: str, _bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    ban = WORLD.bans.get((guild_id, user_id))
    if not ban:
        raise HTTPException(status_code=404, detail={"code": 10026, "message": "Unknown Ban"})
    return ban


@router.put("/guilds/{guild_id}/bans/{user_id}", status_code=204)
async def create_ban(guild_id: str, user_id: str, body: dict | None = None, bot: User = Depends(require_bot)):
    _require_guild(guild_id)
    body = body or {}
    reason = body.get("reason")
    _delete_seconds = body.get("delete_message_seconds", 0)  # not retained in emulator
    ban = _ban_dict(user_id, reason)
    WORLD.bans[(guild_id, user_id)] = ban

    user = WORLD.users.get(user_id)
    user_payload = user.to_dict() if user else {"id": user_id}

    _remove_member(guild_id, user_id)

    WORLD.bus.publish("GUILD_BAN_ADD", {"guild_id": guild_id, "user": user_payload})
    WORLD.bus.publish("GUILD_MEMBER_REMOVE", {"guild_id": guild_id, "user": user_payload})
    audit.log(guild_id, bot.id, user_id, 22, reason=reason)  # MEMBER_BAN_ADD


@router.delete("/guilds/{guild_id}/bans/{user_id}", status_code=204)
async def remove_ban(guild_id: str, user_id: str, bot: User = Depends(require_bot)):
    _require_guild(guild_id)
    ban = WORLD.bans.pop((guild_id, user_id), None)
    if not ban:
        raise HTTPException(status_code=404, detail={"code": 10026, "message": "Unknown Ban"})
    user = WORLD.users.get(user_id)
    WORLD.bus.publish("GUILD_BAN_REMOVE", {
        "guild_id": guild_id,
        "user": user.to_dict() if user else {"id": user_id},
    })
    audit.log(guild_id, bot.id, user_id, 23)  # MEMBER_BAN_REMOVE


@router.post("/guilds/{guild_id}/bulk-ban")
async def bulk_ban(guild_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    user_ids = body.get("user_ids") or []
    if not isinstance(user_ids, list):
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
    banned: list[str] = []
    failed: list[str] = []
    reason = body.get("reason")
    for uid in user_ids:
        uid = str(uid)
        if (guild_id, uid) in WORLD.bans:
            failed.append(uid)
            continue
        if uid not in WORLD.users:
            failed.append(uid)
            continue
        ban = _ban_dict(uid, reason)
        WORLD.bans[(guild_id, uid)] = ban
        _remove_member(guild_id, uid)
        user_payload = WORLD.users[uid].to_dict()
        WORLD.bus.publish("GUILD_BAN_ADD", {"guild_id": guild_id, "user": user_payload})
        WORLD.bus.publish("GUILD_MEMBER_REMOVE", {"guild_id": guild_id, "user": user_payload})
        audit.log(guild_id, bot.id, uid, 22, reason=reason)  # MEMBER_BAN_ADD
        banned.append(uid)
    return {"banned_users": banned, "failed_users": failed}

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


@router.get("/users/@me")
async def get_me(bot: User = Depends(require_bot)) -> dict:
    return bot.to_dict(private=True)


@router.get("/users/@me/guilds")
async def get_my_guilds(bot: User = Depends(require_bot)) -> list[dict]:
    result = []
    for g in WORLD.guilds.values():
        if (bot.id, g.id) in WORLD.members:
            result.append({
                "id": g.id,
                "name": g.name,
                "icon": g.icon,
                "banner": None,
                "owner": g.owner_id == bot.id,
                "permissions": "2147483647",
                "features": g.features,
            })
    return result


@router.get("/users/@me/guilds/{guild_id}/member")
async def get_my_guild_member(guild_id: str, bot: User = Depends(require_bot)) -> dict:
    m = WORLD.members.get((bot.id, guild_id))
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return m.to_dict(WORLD.users)


@router.get("/users/{user_id}")
async def get_user(user_id: str, _bot: User = Depends(require_bot)) -> dict:
    u = WORLD.users.get(user_id)
    if not u:
        raise HTTPException(status_code=404, detail={"code": 10013, "message": "Unknown User"})
    return u.to_dict()

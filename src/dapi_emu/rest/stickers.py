from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import audit
from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


def _require_guild(guild_id: str):
    g = WORLD.guilds.get(guild_id)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


def _require_sticker(sticker_id: str) -> dict:
    s = WORLD.stickers.get(sticker_id)
    if not s:
        raise HTTPException(status_code=404, detail={"code": 10060, "message": "Unknown Sticker"})
    return s


def _sticker_for_guild(guild_id: str, sticker_id: str) -> dict:
    ids = WORLD.guild_stickers.get(guild_id, [])
    if sticker_id not in ids:
        raise HTTPException(status_code=404, detail={"code": 10060, "message": "Unknown Sticker"})
    return _require_sticker(sticker_id)


@router.get("/stickers/{sticker_id}")
async def get_sticker(sticker_id: str, _bot: User = Depends(require_bot)) -> dict:
    return _require_sticker(sticker_id)


@router.get("/sticker-packs")
async def list_sticker_packs(_bot: User = Depends(require_bot)) -> dict:
    return {"sticker_packs": list(WORLD.sticker_packs.values())}


@router.get("/guilds/{guild_id}/stickers")
async def list_guild_stickers(guild_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_guild(guild_id)
    ids = WORLD.guild_stickers.get(guild_id, [])
    return [WORLD.stickers[i] for i in ids if i in WORLD.stickers]


@router.get("/guilds/{guild_id}/stickers/{sticker_id}")
async def get_guild_sticker(guild_id: str, sticker_id: str, _bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    return _sticker_for_guild(guild_id, sticker_id)


async def _parse_sticker_body(request: Request) -> dict:
    """Accept either multipart/form-data or JSON for create/patch."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        out: dict = {}
        for k in ("name", "description", "tags"):
            if k in form:
                out[k] = str(form[k])
        if "file" in form:
            out["file"] = form["file"]
        return out
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/guilds/{guild_id}/stickers", status_code=201)
async def create_guild_sticker(guild_id: str, request: Request, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    body = await _parse_sticker_body(request)
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
    sid = new_snowflake()
    sticker = {
        "id": sid,
        "pack_id": None,
        "name": name,
        "description": body.get("description") or "",
        "tags": body.get("tags") or "",
        "asset": "",
        "type": 2,          # GUILD
        "format_type": 1,   # PNG
        "available": True,
        "guild_id": guild_id,
        "user": bot.to_dict(),
        "sort_value": 0,
    }
    WORLD.stickers[sid] = sticker
    WORLD.guild_stickers.setdefault(guild_id, []).append(sid)
    WORLD.bus.publish("GUILD_STICKERS_UPDATE", {
        "guild_id": guild_id,
        "stickers": [WORLD.stickers[i] for i in WORLD.guild_stickers.get(guild_id, []) if i in WORLD.stickers],
    })
    audit.log(  # STICKER_CREATE
        guild_id, bot.id, sid, 100,
        changes=[{"key": "name", "new_value": name}],
    )
    return sticker


@router.patch("/guilds/{guild_id}/stickers/{sticker_id}")
async def edit_guild_sticker(guild_id: str, sticker_id: str, request: Request, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    sticker = _sticker_for_guild(guild_id, sticker_id)
    body = await _parse_sticker_body(request)
    changes: list[dict] = []
    for field in ("name", "description", "tags"):
        if field in body:
            old = sticker.get(field)
            new = body[field]
            if old != new:
                changes.append({"key": field, "old_value": old, "new_value": new})
            sticker[field] = new
    WORLD.bus.publish("GUILD_STICKERS_UPDATE", {
        "guild_id": guild_id,
        "stickers": [WORLD.stickers[i] for i in WORLD.guild_stickers.get(guild_id, []) if i in WORLD.stickers],
    })
    audit.log(guild_id, bot.id, sticker_id, 101, changes=changes)  # STICKER_UPDATE
    return sticker


@router.delete("/guilds/{guild_id}/stickers/{sticker_id}", status_code=204)
async def delete_guild_sticker(guild_id: str, sticker_id: str, bot: User = Depends(require_bot)):
    _require_guild(guild_id)
    sticker = _sticker_for_guild(guild_id, sticker_id)
    name = sticker.get("name")
    WORLD.stickers.pop(sticker_id, None)
    lst = WORLD.guild_stickers.get(guild_id, [])
    if sticker_id in lst:
        lst.remove(sticker_id)
    WORLD.bus.publish("GUILD_STICKERS_UPDATE", {
        "guild_id": guild_id,
        "stickers": [WORLD.stickers[i] for i in WORLD.guild_stickers.get(guild_id, []) if i in WORLD.stickers],
    })
    audit.log(  # STICKER_DELETE
        guild_id, bot.id, sticker_id, 102,
        changes=[{"key": "name", "old_value": name}],
    )

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

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


def _emoji_for_guild(guild_id: str, emoji_id: str) -> dict:
    ids = WORLD.guild_emojis.get(guild_id, [])
    if emoji_id not in ids:
        raise HTTPException(status_code=404, detail={"code": 10014, "message": "Unknown Emoji"})
    e = WORLD.emojis.get(emoji_id)
    if not e:
        raise HTTPException(status_code=404, detail={"code": 10014, "message": "Unknown Emoji"})
    return e


def _new_emoji(name: str, roles: list[str] | None, user: User, *, animated: bool = False,
               guild_id: str | None = None, application_id: str | None = None) -> dict:
    eid = new_snowflake()
    emoji = {
        "id": eid,
        "name": name,
        "roles": list(roles or []),
        "user": user.to_dict(),
        "require_colons": True,
        "managed": False,
        "animated": bool(animated),
        "available": True,
    }
    if guild_id is not None:
        emoji["guild_id"] = guild_id
    if application_id is not None:
        emoji["application_id"] = application_id
    WORLD.emojis[eid] = emoji
    return emoji


# --- Guild emojis ---------------------------------------------------------

@router.get("/guilds/{guild_id}/emojis")
async def list_guild_emojis(guild_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_guild(guild_id)
    ids = WORLD.guild_emojis.get(guild_id, [])
    return [WORLD.emojis[i] for i in ids if i in WORLD.emojis]


@router.get("/guilds/{guild_id}/emojis/{emoji_id}")
async def get_guild_emoji(guild_id: str, emoji_id: str, _bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    return _emoji_for_guild(guild_id, emoji_id)


@router.post("/guilds/{guild_id}/emojis", status_code=201)
async def create_guild_emoji(guild_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
    # `image` is an accepted data URL but emulator does not store image bytes.
    animated = False
    image = body.get("image")
    if isinstance(image, str) and image.startswith("data:image/gif"):
        animated = True
    emoji = _new_emoji(name, body.get("roles"), bot, animated=animated, guild_id=guild_id)
    WORLD.guild_emojis.setdefault(guild_id, []).append(emoji["id"])
    WORLD.bus.publish("GUILD_EMOJIS_UPDATE", {
        "guild_id": guild_id,
        "emojis": [WORLD.emojis[i] for i in WORLD.guild_emojis.get(guild_id, []) if i in WORLD.emojis],
    })
    audit.log(  # EMOJI_CREATE
        guild_id, bot.id, emoji["id"], 60,
        changes=[{"key": "name", "new_value": emoji["name"]}],
    )
    return emoji


@router.patch("/guilds/{guild_id}/emojis/{emoji_id}")
async def edit_guild_emoji(guild_id: str, emoji_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    emoji = _emoji_for_guild(guild_id, emoji_id)
    changes: list[dict] = []
    if "name" in body:
        old = emoji.get("name")
        new = body["name"]
        if old != new:
            changes.append({"key": "name", "old_value": old, "new_value": new})
        emoji["name"] = new
    if "roles" in body:
        emoji["roles"] = list(body["roles"] or [])
    WORLD.bus.publish("GUILD_EMOJIS_UPDATE", {
        "guild_id": guild_id,
        "emojis": [WORLD.emojis[i] for i in WORLD.guild_emojis.get(guild_id, []) if i in WORLD.emojis],
    })
    audit.log(guild_id, bot.id, emoji_id, 61, changes=changes)  # EMOJI_UPDATE
    return emoji


@router.delete("/guilds/{guild_id}/emojis/{emoji_id}", status_code=204)
async def delete_guild_emoji(guild_id: str, emoji_id: str, bot: User = Depends(require_bot)):
    _require_guild(guild_id)
    emoji = _emoji_for_guild(guild_id, emoji_id)
    name = emoji.get("name")
    WORLD.emojis.pop(emoji_id, None)
    lst = WORLD.guild_emojis.get(guild_id, [])
    if emoji_id in lst:
        lst.remove(emoji_id)
    WORLD.bus.publish("GUILD_EMOJIS_UPDATE", {
        "guild_id": guild_id,
        "emojis": [WORLD.emojis[i] for i in WORLD.guild_emojis.get(guild_id, []) if i in WORLD.emojis],
    })
    audit.log(  # EMOJI_DELETE
        guild_id, bot.id, emoji_id, 62,
        changes=[{"key": "name", "old_value": name}],
    )


# --- Application emojis ---------------------------------------------------

def _require_application(application_id: str):
    app = WORLD.applications.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})
    return app


def _app_emoji_ids(application_id: str) -> list[str]:
    # reuse guild_emojis dict keyed by application_id for simplicity;
    # distinct namespace from guilds because IDs won't collide with real guild IDs.
    return WORLD.guild_emojis.setdefault(f"app:{application_id}", [])


@router.get("/applications/{application_id}/emojis")
async def list_app_emojis(application_id: str, _bot: User = Depends(require_bot)) -> dict:
    _require_application(application_id)
    ids = _app_emoji_ids(application_id)
    return {"items": [WORLD.emojis[i] for i in ids if i in WORLD.emojis]}


@router.post("/applications/{application_id}/emojis", status_code=201)
async def create_app_emoji(application_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    _require_application(application_id)
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
    animated = False
    image = body.get("image")
    if isinstance(image, str) and image.startswith("data:image/gif"):
        animated = True
    emoji = _new_emoji(name, body.get("roles"), bot, animated=animated, application_id=application_id)
    _app_emoji_ids(application_id).append(emoji["id"])
    return emoji


@router.get("/applications/{application_id}/emojis/{emoji_id}")
async def get_app_emoji(application_id: str, emoji_id: str, _bot: User = Depends(require_bot)) -> dict:
    _require_application(application_id)
    ids = _app_emoji_ids(application_id)
    if emoji_id not in ids or emoji_id not in WORLD.emojis:
        raise HTTPException(status_code=404, detail={"code": 10014, "message": "Unknown Emoji"})
    return WORLD.emojis[emoji_id]


@router.patch("/applications/{application_id}/emojis/{emoji_id}")
async def edit_app_emoji(application_id: str, emoji_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    _require_application(application_id)
    ids = _app_emoji_ids(application_id)
    if emoji_id not in ids or emoji_id not in WORLD.emojis:
        raise HTTPException(status_code=404, detail={"code": 10014, "message": "Unknown Emoji"})
    emoji = WORLD.emojis[emoji_id]
    if "name" in body:
        emoji["name"] = body["name"]
    return emoji


@router.delete("/applications/{application_id}/emojis/{emoji_id}", status_code=204)
async def delete_app_emoji(application_id: str, emoji_id: str, _bot: User = Depends(require_bot)):
    _require_application(application_id)
    ids = _app_emoji_ids(application_id)
    if emoji_id not in ids or emoji_id not in WORLD.emojis:
        raise HTTPException(status_code=404, detail={"code": 10014, "message": "Unknown Emoji"})
    WORLD.emojis.pop(emoji_id, None)
    ids.remove(emoji_id)

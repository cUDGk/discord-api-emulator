from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


_REGIONS: list[dict[str, Any]] = [
    {"id": "us-east", "name": "US East", "optimal": True, "deprecated": False, "custom": False},
    {"id": "us-west", "name": "US West", "optimal": False, "deprecated": False, "custom": False},
    {"id": "japan", "name": "Japan", "optimal": False, "deprecated": False, "custom": False},
]


def _require_guild(guild_id: str):
    g = WORLD.guilds.get(guild_id)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


def _require_channel(channel_id: str):
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return ch


def _default_voice_state(guild_id: str, user_id: str) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "channel_id": None,
        "user_id": user_id,
        "session_id": f"session-{user_id}-{guild_id}",
        "deaf": False,
        "mute": False,
        "self_deaf": False,
        "self_mute": False,
        "self_video": False,
        "suppress": False,
        "request_to_speak_timestamp": None,
    }


def _get_or_create_voice_state(guild_id: str, user_id: str) -> dict[str, Any]:
    key = (guild_id, user_id)
    vs = WORLD.voice_states.get(key)
    if not vs:
        vs = _default_voice_state(guild_id, user_id)
        WORLD.voice_states[key] = vs
    return vs


def _with_member(vs: dict[str, Any]) -> dict[str, Any]:
    out = dict(vs)
    gid = vs.get("guild_id")
    uid = vs.get("user_id")
    if gid and uid and (uid, gid) in WORLD.members:
        out["member"] = WORLD.members[(uid, gid)].to_dict(WORLD.users)
    return out


@router.get("/voice/regions")
async def list_voice_regions(_bot: User = Depends(require_bot)) -> list[dict]:
    return list(_REGIONS)


@router.get("/guilds/{guild_id}/voice-regions")
async def list_guild_voice_regions(guild_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_guild(guild_id)
    return list(_REGIONS)


@router.get("/guilds/{guild_id}/voice-states/@me")
async def get_own_voice_state(guild_id: str, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    vs = WORLD.voice_states.get((guild_id, bot.id))
    if not vs:
        raise HTTPException(status_code=404, detail={"code": 10065, "message": "Unknown Voice State"})
    return _with_member(vs)


@router.get("/guilds/{guild_id}/voice-states/{user_id}")
async def get_voice_state(guild_id: str, user_id: str, _bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    vs = WORLD.voice_states.get((guild_id, user_id))
    if not vs:
        raise HTTPException(status_code=404, detail={"code": 10065, "message": "Unknown Voice State"})
    return _with_member(vs)


@router.patch("/guilds/{guild_id}/voice-states/@me", status_code=204)
async def edit_own_voice_state(guild_id: str, body: dict, bot: User = Depends(require_bot)):
    _require_guild(guild_id)
    channel_id = body.get("channel_id")
    if channel_id is not None:
        ch = _require_channel(channel_id)
        # stage channels are type 13
        if getattr(ch, "type", None) != 13:
            raise HTTPException(status_code=400, detail={"code": 50007, "message": "Target is not a stage channel"})
    vs = _get_or_create_voice_state(guild_id, bot.id)
    if channel_id is not None:
        vs["channel_id"] = channel_id
    if "suppress" in body:
        vs["suppress"] = bool(body["suppress"])
    if "request_to_speak_timestamp" in body:
        ts = body["request_to_speak_timestamp"]
        if ts is None:
            vs["request_to_speak_timestamp"] = None
        else:
            vs["request_to_speak_timestamp"] = ts
            # requesting to speak resets suppress state to True by convention
    WORLD.bus.publish("VOICE_STATE_UPDATE", _with_member(vs))


@router.patch("/guilds/{guild_id}/voice-states/{user_id}", status_code=204)
async def edit_voice_state(guild_id: str, user_id: str, body: dict, _bot: User = Depends(require_bot)):
    _require_guild(guild_id)
    channel_id = body.get("channel_id")
    if channel_id is not None:
        ch = _require_channel(channel_id)
        if getattr(ch, "type", None) != 13:
            raise HTTPException(status_code=400, detail={"code": 50007, "message": "Target is not a stage channel"})
    vs = _get_or_create_voice_state(guild_id, user_id)
    if channel_id is not None:
        vs["channel_id"] = channel_id
    if "suppress" in body:
        # when un-suppressing another user, request_to_speak is implicitly cleared
        vs["suppress"] = bool(body["suppress"])
        if not vs["suppress"]:
            vs["request_to_speak_timestamp"] = datetime.now(timezone.utc).isoformat()
    WORLD.bus.publish("VOICE_STATE_UPDATE", _with_member(vs))

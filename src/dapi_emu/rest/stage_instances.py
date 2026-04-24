from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_channel(channel_id: str):
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return ch


def _require_stage(channel_id: str) -> dict[str, Any]:
    stage = WORLD.stage_instances.get(channel_id)
    if not stage:
        raise HTTPException(status_code=404, detail={"code": 10067, "message": "Unknown Stage Instance"})
    return stage


@router.post("/stage-instances", status_code=201)
async def create_stage_instance(body: dict, _bot: User = Depends(require_bot)) -> dict:
    channel_id = body.get("channel_id")
    if not channel_id:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "channel_id is required"})
    ch = _require_channel(channel_id)
    if getattr(ch, "type", None) != 13:
        raise HTTPException(status_code=400, detail={"code": 50007, "message": "Target is not a stage channel"})
    if channel_id in WORLD.stage_instances:
        raise HTTPException(status_code=400, detail={"code": 150006, "message": "Stage instance already exists"})
    topic = body.get("topic")
    if not topic:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "topic is required"})
    stage = {
        "id": channel_id,
        "guild_id": ch.guild_id,
        "channel_id": channel_id,
        "topic": topic,
        "privacy_level": int(body.get("privacy_level", 2)),
        "discoverable_disabled": True,
        "guild_scheduled_event_id": body.get("guild_scheduled_event_id"),
    }
    WORLD.stage_instances[channel_id] = stage
    WORLD.bus.publish("STAGE_INSTANCE_CREATE", stage)
    return stage


@router.get("/stage-instances/{channel_id}")
async def get_stage_instance(channel_id: str, _bot: User = Depends(require_bot)) -> dict:
    return _require_stage(channel_id)


@router.patch("/stage-instances/{channel_id}")
async def edit_stage_instance(channel_id: str, body: dict, _bot: User = Depends(require_bot)) -> dict:
    stage = _require_stage(channel_id)
    if "topic" in body:
        stage["topic"] = body["topic"]
    if "privacy_level" in body:
        stage["privacy_level"] = int(body["privacy_level"])
    WORLD.bus.publish("STAGE_INSTANCE_UPDATE", stage)
    return stage


@router.delete("/stage-instances/{channel_id}", status_code=204)
async def delete_stage_instance(channel_id: str, _bot: User = Depends(require_bot)):
    stage = _require_stage(channel_id)
    WORLD.stage_instances.pop(channel_id, None)
    WORLD.bus.publish("STAGE_INSTANCE_DELETE", stage)

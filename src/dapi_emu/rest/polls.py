from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_message(channel_id: str, message_id: str):
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    return m


def _require_poll(message_id: str) -> dict[str, Any]:
    poll = WORLD.polls.get(message_id)
    if not poll:
        raise HTTPException(status_code=404, detail={"code": 10083, "message": "Unknown Poll"})
    return poll


@router.get("/channels/{channel_id}/polls/{message_id}/answers/{answer_id}")
async def list_poll_voters(
    channel_id: str,
    message_id: str,
    answer_id: str,
    after: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    _bot: User = Depends(require_bot),
) -> dict:
    _require_message(channel_id, message_id)
    poll = _require_poll(message_id)
    # answer existence check (int id in poll.answers)
    answers = poll.get("answers", [])
    if not any(str(a.get("answer_id")) == str(answer_id) for a in answers):
        raise HTTPException(status_code=404, detail={"code": 10086, "message": "Unknown Poll Answer"})
    return {"users": []}


@router.post("/channels/{channel_id}/polls/{message_id}/expire")
async def expire_poll(
    channel_id: str,
    message_id: str,
    _bot: User = Depends(require_bot),
) -> dict:
    m = _require_message(channel_id, message_id)
    poll = _require_poll(message_id)
    results = poll.setdefault("results", {"is_finalized": False, "answer_counts": []})
    results["is_finalized"] = True
    poll["expiry"] = datetime.now(timezone.utc).isoformat()
    # reflect poll on the message
    setattr(m, "poll", poll)
    payload = m.to_dict(WORLD.users)
    payload["poll"] = poll
    WORLD.bus.publish("MESSAGE_UPDATE", payload)
    return payload

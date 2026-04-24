from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_guild(guild_id: str):
    g = WORLD.guilds.get(guild_id)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


@router.get("/guilds/{guild_id}/audit-logs")
async def get_audit_log(
    guild_id: str,
    user_id: str | None = None,
    action_type: int | None = None,
    before: str | None = None,
    after: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    _bot: User = Depends(require_bot),
) -> dict:
    _require_guild(guild_id)
    entries = list(WORLD.audit_logs.get(guild_id, []))

    if user_id is not None:
        entries = [e for e in entries if e.get("user_id") == user_id]
    if action_type is not None:
        entries = [e for e in entries if e.get("action_type") == action_type]

    # newest first by snowflake id
    entries.sort(key=lambda e: int(e.get("id", 0)), reverse=True)
    if before:
        entries = [e for e in entries if int(e.get("id", 0)) < int(before)]
    elif after:
        entries = [e for e in entries if int(e.get("id", 0)) > int(after)]
        entries.reverse()
    entries = entries[:limit]

    # Collect referenced users for the `users` sidecar.
    user_ids: set[str] = set()
    for e in entries:
        uid = e.get("user_id")
        if uid:
            user_ids.add(uid)
        tid = e.get("target_id")
        if tid and tid in WORLD.users:
            user_ids.add(tid)
    users = [WORLD.users[uid].to_dict() for uid in user_ids if uid in WORLD.users]

    return {
        "audit_log_entries": entries,
        "users": users,
        "integrations": [],
        "webhooks": [],
        "threads": [],
        "application_commands": [],
        "auto_moderation_rules": [],
        "guild_scheduled_events": [],
    }

"""Audit-log helper. Pure side-effect: append an entry to WORLD.audit_logs.

REST handlers call `audit.log(...)` after a successful mutation. The GET
endpoint at /guilds/{id}/audit-logs reads from the same dict.
"""
from __future__ import annotations

from typing import Any, Optional

from .snowflake import generate as new_snowflake
from .state import WORLD


def log(
    guild_id: Optional[str],
    user_id: Optional[str],
    target_id: Optional[str],
    action_type: int,
    *,
    changes: Optional[list[dict[str, Any]]] = None,
    options: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> None:
    """Append an audit-log entry to the given guild. No-op if guild_id is None."""
    if not guild_id:
        return
    entry: dict[str, Any] = {
        "id": new_snowflake(),
        "user_id": user_id,
        "target_id": target_id,
        "action_type": action_type,
        "changes": changes or [],
        "options": options or {},
        "reason": reason,
    }
    WORLD.audit_logs.setdefault(guild_id, []).append(entry)

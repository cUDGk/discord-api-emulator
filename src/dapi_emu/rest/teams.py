from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _require_team(team_id: str) -> dict:
    team = WORLD.teams.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail={"code": 10065, "message": "Unknown Team"})
    return team


def _team_payload(team: dict) -> dict:
    members = []
    for m in team.get("members", []):
        uid = m.get("user_id") if isinstance(m, dict) else None
        user = WORLD.users.get(uid) if uid else None
        members.append({
            "membership_state": m.get("membership_state", 2) if isinstance(m, dict) else 2,
            "team_id": team["id"],
            "user": user.to_dict() if user else m.get("user") if isinstance(m, dict) else None,
            "role": m.get("role", "owner") if isinstance(m, dict) else "owner",
        })
    return {
        "id": team["id"],
        "name": team.get("name", ""),
        "icon": team.get("icon"),
        "owner_user_id": team.get("owner_user_id"),
        "members": members,
    }


@router.get("/teams/{team_id}")
async def get_team(team_id: str, _bot: User = Depends(require_bot)) -> dict:
    team = _require_team(team_id)
    return _team_payload(team)


@router.get("/applications/{application_id}/teams/{team_id}")
async def get_application_team(
    application_id: str,
    team_id: str,
    _bot: User = Depends(require_bot),
) -> dict:
    app = WORLD.applications.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})
    team = _require_team(team_id)
    return _team_payload(team)

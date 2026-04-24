from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_bot
from ..state import WORLD, User

router = APIRouter()


def _app_for_bot(bot: User):
    for app in WORLD.applications.values():
        if app.bot_id == bot.id:
            return app
    return None


@router.get("/applications/@me")
async def get_my_application(bot: User = Depends(require_bot)) -> dict:
    app = _app_for_bot(bot)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})
    return app.to_dict(WORLD.users)


@router.get("/oauth2/applications/@me")
async def get_oauth2_app(bot: User = Depends(require_bot)) -> dict:
    app = _app_for_bot(bot)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})
    return app.to_dict(WORLD.users)

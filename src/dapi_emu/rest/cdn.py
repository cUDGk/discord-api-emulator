from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()


TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


def _png() -> Response:
    return Response(content=TRANSPARENT_PNG, media_type="image/png")


@router.get("/cdn/avatars/{user_id}/{avatar_hash}.{ext}")
async def cdn_avatar(user_id: str, avatar_hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/icons/{guild_id}/{icon_hash}.{ext}")
async def cdn_icon(guild_id: str, icon_hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/splashes/{guild_id}/{hash}.{ext}")
async def cdn_splash(guild_id: str, hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/banners/{id}/{hash}.{ext}")
async def cdn_banner(id: str, hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/embed/avatars/{index}.png")
async def cdn_default_avatar(index: str) -> Response:
    return _png()


@router.get("/cdn/emojis/{emoji_id}.{ext}")
async def cdn_emoji(emoji_id: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/stickers/{sticker_id}.{ext}")
async def cdn_sticker(sticker_id: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/app-icons/{application_id}/{icon_hash}.{ext}")
async def cdn_app_icon(application_id: str, icon_hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/app-assets/{application_id}/{asset_id}.{ext}")
async def cdn_app_asset(application_id: str, asset_id: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/team-icons/{team_id}/{team_icon_hash}.{ext}")
async def cdn_team_icon(team_id: str, team_icon_hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/role-icons/{role_id}/{role_icon_hash}.{ext}")
async def cdn_role_icon(role_id: str, role_icon_hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/guild-events/{event_id}/{hash}.{ext}")
async def cdn_guild_event(event_id: str, hash: str, ext: str) -> Response:
    return _png()


@router.get("/cdn/achievements/{application_id}/achievements/{achievement_id}/icons/{icon_hash}.{ext}")
async def cdn_achievement(application_id: str, achievement_id: str, icon_hash: str, ext: str) -> Response:
    return _png()

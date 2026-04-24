"""CDN endpoints.

Returns deterministic, identifiable placeholder PNGs so that apps wired against
this emulator can visually distinguish users/guilds/emojis during development
without having to ship real image assets. The rendered color is derived from
the resource id (so the same avatar always looks the same), and an initials
label is drawn from whatever name we can recover from ``WORLD``.

If Pillow is unavailable for any reason the router falls back to the legacy
1x1 transparent PNG. This keeps the emulator functional on bare installs.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from io import BytesIO
from typing import Callable

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ..state import WORLD

router = APIRouter()


# 1x1 transparent PNG — used as a fallback when Pillow/font are unavailable.
TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:  # pragma: no cover - import guard
    _PIL_OK = False


# ---- Color + label helpers ------------------------------------------------

# Discord's 6 default avatar slots, index 0..5 (Blurple/Gray/Green/Yellow/Red/Pink).
_DEFAULT_AVATAR_COLORS: tuple[tuple[int, int, int], ...] = (
    (88, 101, 242),   # 0 blurple
    (116, 127, 141),  # 1 gray
    (87, 242, 135),   # 2 green
    (254, 231, 92),   # 3 yellow
    (237, 66, 69),    # 4 red
    (235, 69, 158),   # 5 pink
)


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    # Standard HSL -> RGB, hue in [0,1].
    if s == 0:
        v = int(round(l * 255))
        return v, v, v
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def f(t: float) -> float:
        t %= 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    return (
        int(round(f(h + 1 / 3) * 255)),
        int(round(f(h) * 255)),
        int(round(f(h - 1 / 3) * 255)),
    )


def _color_from(seed: str) -> tuple[int, int, int]:
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    hue = h[0] / 255.0
    return _hsl_to_rgb(hue, 0.55, 0.55)


def _initials(name: str | None) -> str:
    if not name:
        return "?"
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _clamp_size(size: int) -> int:
    if size < 16:
        return 16
    if size > 4096:
        return 4096
    return size


def _load_font(px: int) -> "ImageFont.ImageFont | ImageFont.FreeTypeFont":
    # Try a few font names common on Windows/Linux/macOS before giving up to
    # the bitmap default (which ignores the requested size but still renders).
    for candidate in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "Arial.ttf"):
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=512)
def _render(seed: str, label: str, size: int, rgb: tuple[int, int, int] | None = None) -> bytes:
    """Render and cache a placeholder PNG. Returns raw bytes."""
    if not _PIL_OK:
        return TRANSPARENT_PNG
    color = rgb if rgb is not None else _color_from(seed)
    img = Image.new("RGBA", (size, size), color + (255,))
    draw = ImageDraw.Draw(img)
    font = _load_font(max(8, size // 3))
    # textbbox -> (x0, y0, x1, y1); width/height = x1-x0, y1-y0.
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # Offset by bbox origin so the glyph sits centered even when the font has
    # leading whitespace above the cap height.
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1] - size // 20
    draw.text((x, y), label, fill=(255, 255, 255, 255), font=font)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _png_response(payload: bytes) -> Response:
    return Response(
        content=payload,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _render_or_fallback(seed: str, label: str, size: int,
                        rgb: tuple[int, int, int] | None = None) -> Response:
    try:
        data = _render(seed, label, _clamp_size(size), rgb)
    except Exception:
        data = TRANSPARENT_PNG
    return _png_response(data)


# ---- Name lookup helpers (read-only) --------------------------------------

def _user_name(user_id: str) -> str | None:
    u = WORLD.users.get(user_id)
    if u is None:
        return None
    return u.global_name or u.username


def _guild_name(guild_id: str) -> str | None:
    g = WORLD.guilds.get(guild_id)
    return g.name if g else None


def _emoji_name(emoji_id: str) -> str | None:
    e = WORLD.emojis.get(emoji_id)
    if not e:
        return None
    name = e.get("name")
    return str(name) if name else None


def _sticker_name(sticker_id: str) -> str | None:
    s = WORLD.stickers.get(sticker_id)
    if not s:
        return None
    name = s.get("name")
    return str(name) if name else None


def _application_name(application_id: str) -> str | None:
    a = WORLD.applications.get(application_id)
    return a.name if a else None


def _team_name(team_id: str) -> str | None:
    t = WORLD.teams.get(team_id)
    if not t:
        return None
    name = t.get("name")
    return str(name) if name else None


def _role_name(role_id: str) -> str | None:
    r = WORLD.roles.get(role_id)
    return r.name if r else None


def _event_name(event_id: str) -> str | None:
    ev = WORLD.scheduled_events.get(event_id)
    if not ev:
        return None
    name = ev.get("name")
    return str(name) if name else None


def _id_fallback_initials(resource_id: str) -> str:
    """Derive two initials from the tail of an id when no name is known."""
    tail = resource_id[-4:] if resource_id else "??"
    # Map digits to letters for visual stability.
    letters = "".join(chr(ord("A") + (int(c) if c.isdigit() else ord(c)) % 26) for c in tail)
    return letters[:2].upper() or "??"


def _lookup_label(resource_id: str, lookup: Callable[[str], str | None]) -> str:
    name = lookup(resource_id)
    if name:
        return _initials(name)
    return _id_fallback_initials(resource_id)


# ---- Endpoints ------------------------------------------------------------

@router.get("/cdn/avatars/{user_id}/{avatar_hash}.{ext}")
async def cdn_avatar(user_id: str, avatar_hash: str, ext: str,
                     size: int = Query(256)) -> Response:
    label = _lookup_label(user_id, _user_name)
    return _render_or_fallback(user_id, label, size)


@router.get("/cdn/icons/{guild_id}/{icon_hash}.{ext}")
async def cdn_icon(guild_id: str, icon_hash: str, ext: str,
                   size: int = Query(256)) -> Response:
    label = _lookup_label(guild_id, _guild_name)
    return _render_or_fallback(guild_id, label, size)


@router.get("/cdn/splashes/{guild_id}/{hash}.{ext}")
async def cdn_splash(guild_id: str, hash: str, ext: str,
                     size: int = Query(256)) -> Response:
    # Splashes are intentionally label-less — just a solid color plate.
    return _render_or_fallback(guild_id, "", size)


@router.get("/cdn/banners/{id}/{hash}.{ext}")
async def cdn_banner(id: str, hash: str, ext: str,
                     size: int = Query(256)) -> Response:
    return _render_or_fallback(id, "", size)


@router.get("/cdn/embed/avatars/{index}.png")
async def cdn_default_avatar(index: str, size: int = Query(256)) -> Response:
    try:
        idx = int(index) % len(_DEFAULT_AVATAR_COLORS)
    except ValueError:
        idx = 0
    color = _DEFAULT_AVATAR_COLORS[idx]
    # Seed is the numeric index so the cache is tiny and stable.
    return _render_or_fallback(f"default:{idx}", "", size, rgb=color)


@router.get("/cdn/emojis/{emoji_id}.{ext}")
async def cdn_emoji(emoji_id: str, ext: str,
                    size: int = Query(256)) -> Response:
    name = _emoji_name(emoji_id)
    label = _initials(name) if name else ":)"
    return _render_or_fallback(emoji_id, label, size)


@router.get("/cdn/stickers/{sticker_id}.{ext}")
async def cdn_sticker(sticker_id: str, ext: str,
                      size: int = Query(256)) -> Response:
    label = _lookup_label(sticker_id, _sticker_name)
    return _render_or_fallback(sticker_id, label, size)


@router.get("/cdn/app-icons/{application_id}/{icon_hash}.{ext}")
async def cdn_app_icon(application_id: str, icon_hash: str, ext: str,
                       size: int = Query(256)) -> Response:
    label = _lookup_label(application_id, _application_name)
    return _render_or_fallback(application_id, label, size)


@router.get("/cdn/app-assets/{application_id}/{asset_id}.{ext}")
async def cdn_app_asset(application_id: str, asset_id: str, ext: str,
                        size: int = Query(256)) -> Response:
    # Seed by asset_id so each asset of an app looks distinct.
    return _render_or_fallback(f"{application_id}:{asset_id}", "ASSET", size)


@router.get("/cdn/team-icons/{team_id}/{team_icon_hash}.{ext}")
async def cdn_team_icon(team_id: str, team_icon_hash: str, ext: str,
                        size: int = Query(256)) -> Response:
    label = _lookup_label(team_id, _team_name)
    return _render_or_fallback(team_id, label, size)


@router.get("/cdn/role-icons/{role_id}/{role_icon_hash}.{ext}")
async def cdn_role_icon(role_id: str, role_icon_hash: str, ext: str,
                        size: int = Query(256)) -> Response:
    label = _lookup_label(role_id, _role_name)
    return _render_or_fallback(role_id, label, size)


@router.get("/cdn/guild-events/{event_id}/{hash}.{ext}")
async def cdn_guild_event(event_id: str, hash: str, ext: str,
                          size: int = Query(256)) -> Response:
    label = _lookup_label(event_id, _event_name)
    return _render_or_fallback(event_id, label, size)


@router.get("/cdn/achievements/{application_id}/achievements/{achievement_id}/icons/{icon_hash}.{ext}")
async def cdn_achievement(application_id: str, achievement_id: str, icon_hash: str, ext: str,
                          size: int = Query(256)) -> Response:
    return _render_or_fallback(f"{application_id}:{achievement_id}", "★", size)

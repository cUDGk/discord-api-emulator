"""CDN router tests: dynamic placeholder PNG generation."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from dapi_emu.app import create_app
from dapi_emu.rest import cdn as cdn_module
from dapi_emu.state import WORLD


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _fresh_client() -> TestClient:
    WORLD.users.clear(); WORLD.guilds.clear(); WORLD.channels.clear()
    WORLD.messages.clear(); WORLD.channel_messages.clear()
    WORLD.roles.clear(); WORLD.members.clear()
    WORLD.applications.clear(); WORLD.bot_tokens.clear()
    WORLD.emojis.clear(); WORLD.stickers.clear()
    # Also clear the render cache — stale entries would make size assertions
    # depend on test ordering.
    cdn_module._render.cache_clear()
    return TestClient(create_app())


def test_default_embed_avatar_returns_real_png():
    c = _fresh_client()
    r = c.get("/api/v10/cdn/embed/avatars/0.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(PNG_MAGIC)
    assert len(r.content) >= 200


def test_default_embed_avatar_indexes_differ():
    c = _fresh_client()
    r0 = c.get("/api/v10/cdn/embed/avatars/0.png").content
    r3 = c.get("/api/v10/cdn/embed/avatars/3.png").content
    assert r0 != r3  # different colors -> different bytes


def test_user_avatar_uses_username_initials():
    c = _fresh_client()
    resp = c.post("/admin/users", json={"username": "alice"})
    assert resp.status_code == 200
    uid = resp.json()["user"]["id"]

    r = c.get(f"/api/v10/cdn/avatars/{uid}/abc.png?size=128")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(PNG_MAGIC)
    # Real rendered 128x128 PNG with text comfortably exceeds 1KB.
    assert len(r.content) > 1000
    assert "max-age" in r.headers.get("cache-control", "")


def test_render_cache_serves_identical_bytes():
    c = _fresh_client()
    resp = c.post("/admin/users", json={"username": "bob"})
    uid = resp.json()["user"]["id"]
    url = f"/api/v10/cdn/avatars/{uid}/xyz.png?size=96"

    cdn_module._render.cache_clear()
    first = c.get(url).content
    info_before = cdn_module._render.cache_info()
    second = c.get(url).content
    info_after = cdn_module._render.cache_info()

    assert first == second
    # Second call must have been served from the lru_cache.
    assert info_after.hits == info_before.hits + 1


def test_guild_icon_and_size_clamp():
    c = _fresh_client()
    owner = c.post("/admin/users", json={"username": "owner"}).json()["user"]["id"]
    gid = c.post("/admin/guilds", json={"name": "My Server", "owner_id": owner}).json()["id"]

    # Oversized size gets clamped (shouldn't explode memory/time).
    r = c.get(f"/api/v10/cdn/icons/{gid}/hash.png?size=99999")
    assert r.status_code == 200
    assert r.content.startswith(PNG_MAGIC)


def test_unknown_user_still_renders_fallback_initials():
    c = _fresh_client()
    r = c.get("/api/v10/cdn/avatars/123456789012345678/aaa.png?size=64")
    assert r.status_code == 200
    assert r.content.startswith(PNG_MAGIC)
    assert len(r.content) > 200

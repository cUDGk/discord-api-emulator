"""Basic smoke tests. Run: python -m pytest tests/ -v"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from dapi_emu.app import create_app
from dapi_emu.state import WORLD


def _fresh_client() -> TestClient:
    # Reset world for test isolation.
    WORLD.users.clear(); WORLD.guilds.clear(); WORLD.channels.clear()
    WORLD.messages.clear(); WORLD.channel_messages.clear()
    WORLD.roles.clear(); WORLD.members.clear()
    WORLD.applications.clear(); WORLD.bot_tokens.clear()
    return TestClient(create_app())


def test_root():
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    assert r.json()["name"] == "dapi-emu"


def test_gateway_info_unauthenticated():
    c = _fresh_client()
    r = c.get("/api/v10/gateway")
    assert r.status_code == 200
    assert "url" in r.json()


def test_full_bot_flow():
    c = _fresh_client()

    # Create bot user
    r = c.post("/admin/users", json={"username": "mybot", "bot": True})
    assert r.status_code == 200
    token = r.json()["token"]
    bot_id = r.json()["user"]["id"]

    # Create owner user + guild
    r = c.post("/admin/users", json={"username": "owner"})
    owner_id = r.json()["user"]["id"]

    r = c.post("/admin/guilds", json={"name": "test-guild", "owner_id": owner_id})
    assert r.status_code == 200
    guild = r.json()
    gid = guild["id"]

    # Add bot to guild
    r = c.post(f"/admin/guilds/{gid}/members", json={"user_id": bot_id})
    assert r.status_code == 200

    headers = {"Authorization": f"Bot {token}"}

    # Bot should see its own info
    r = c.get("/api/v10/users/@me", headers=headers)
    assert r.status_code == 200
    assert r.json()["username"] == "mybot"

    # Bot should see gateway/bot
    r = c.get("/api/v10/gateway/bot", headers=headers)
    assert r.status_code == 200
    assert r.json()["shards"] == 1

    # Bot should see its guilds
    r = c.get("/api/v10/users/@me/guilds", headers=headers)
    assert r.status_code == 200
    assert any(g["id"] == gid for g in r.json())

    # Get channels
    r = c.get(f"/api/v10/guilds/{gid}/channels", headers=headers)
    assert r.status_code == 200
    channels = r.json()
    assert len(channels) >= 1
    ch_id = channels[0]["id"]

    # Send a message
    r = c.post(f"/api/v10/channels/{ch_id}/messages", headers=headers,
               json={"content": "hello"})
    assert r.status_code == 200
    msg = r.json()
    assert msg["content"] == "hello"
    assert msg["author"]["id"] == bot_id

    # List messages
    r = c.get(f"/api/v10/channels/{ch_id}/messages", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_ratelimit_headers_present():
    c = _fresh_client()
    r = c.get("/api/v10/gateway")
    assert r.headers.get("X-RateLimit-Bucket") == "emulator"


def test_snowflake_is_string():
    from dapi_emu.snowflake import generate
    s = generate()
    assert isinstance(s, str)
    assert s.isdigit()

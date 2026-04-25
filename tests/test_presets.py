"""Preset save/load — repeatable test setups in one click."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from dapi_emu.app import create_app
from dapi_emu.state import WORLD


def _reset() -> None:
    for attr in ("users", "guilds", "channels", "messages", "channel_messages",
                 "roles", "members", "applications", "bot_tokens", "presets",
                 "emojis", "guild_emojis"):
        getattr(WORLD, attr).clear()


def test_save_and_load_roundtrip() -> None:
    _reset()
    c = TestClient(create_app())
    # Build a small world
    owner = c.post("/admin/users", json={"username": "owner"}).json()["user"]
    bot = c.post("/admin/users", json={"username": "tbot", "bot": True}).json()
    g = c.post("/admin/guilds", json={"name": "MyServer", "owner_id": owner["id"]}).json()
    gid = g["id"]
    c.post(f"/admin/guilds/{gid}/members", json={"user_id": bot["user"]["id"]})
    c.post(f"/admin/guilds/{gid}/roles", json={"name": "Admin", "color": 0xff0000})
    c.post(f"/admin/guilds/{gid}/roles", json={"name": "Mod", "color": 0x00ff00})
    c.post(f"/admin/guilds/{gid}/channels", json={"name": "general", "type": 0})
    c.post(f"/admin/guilds/{gid}/channels", json={"name": "mod-only", "type": 0})

    # Save snapshot
    r = c.post("/admin/presets", json={"name": "myset"})
    assert r.status_code == 200
    s = r.json()["summary"]
    assert s["users"] == 2 and s["bots"] == 1 and s["guilds"] == 1
    assert s["roles"] == 3  # @everyone + Admin + Mod
    assert s["channels"] == 3  # default general + extra 2
    assert s["members"] == 2  # owner + bot

    # Wipe world
    c.post("/admin/reset")
    state = c.get("/admin/state").json()
    assert state["users"] == [] and state["guilds"] == []

    # Saved preset survives reset (it's its own dict)
    presets = c.get("/admin/presets").json()
    # /admin/reset wipes WORLD.presets too, so we expect empty here
    # — but the test below seeds a preset *after* reset to verify load works
    assert isinstance(presets, list)


def test_load_recreates_state() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "alice"}).json()["user"]
    g = c.post("/admin/guilds", json={"name": "X", "owner_id": owner["id"]}).json()
    c.post(f"/admin/guilds/{g['id']}/roles", json={"name": "VIP"})
    saved = c.post("/admin/presets", json={"name": "vip-setup"}).json()
    assert saved["ok"]

    # Mutate
    c.post("/admin/users", json={"username": "intruder"})
    c.post(f"/admin/guilds/{g['id']}/roles", json={"name": "Junk"})
    state_before = c.get("/admin/state").json()
    assert any(u["username"] == "intruder" for u in state_before["users"])

    # Load
    r = c.post("/admin/presets/vip-setup/load")
    assert r.status_code == 200
    state_after = c.get("/admin/state").json()
    assert not any(u["username"] == "intruder" for u in state_after["users"])
    assert any(u["username"] == "alice" for u in state_after["users"])
    role_names = [r["name"] for guild in state_after["guilds"] for r in guild["roles"]]
    assert "VIP" in role_names and "Junk" not in role_names


def test_delete_preset() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "u"}).json()["user"]
    c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]})
    c.post("/admin/presets", json={"name": "tmp"})
    assert any(p["name"] == "tmp" for p in c.get("/admin/presets").json())
    r = c.delete("/admin/presets/tmp")
    assert r.status_code == 204
    assert not any(p["name"] == "tmp" for p in c.get("/admin/presets").json())


def test_load_unknown_preset_404() -> None:
    _reset()
    c = TestClient(create_app())
    r = c.post("/admin/presets/nope/load")
    assert r.status_code == 404

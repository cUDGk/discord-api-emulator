"""Audit log auto-recording tests.

Run: python -m pytest tests/test_audit_log.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from dapi_emu.app import create_app
from dapi_emu.state import WORLD


def _reset_world() -> None:
    WORLD.users.clear(); WORLD.guilds.clear(); WORLD.channels.clear()
    WORLD.messages.clear(); WORLD.channel_messages.clear()
    WORLD.roles.clear(); WORLD.members.clear()
    WORLD.applications.clear(); WORLD.bot_tokens.clear()
    WORLD.audit_logs.clear()
    WORLD.bans.clear()
    WORLD.invites.clear(); WORLD.guild_invites.clear()
    WORLD.emojis.clear(); WORLD.guild_emojis.clear()
    WORLD.webhooks.clear()
    WORLD.threads.clear(); WORLD.thread_members.clear()
    WORLD.stickers.clear(); WORLD.guild_stickers.clear()
    WORLD.scheduled_events.clear(); WORLD.guild_scheduled_events.clear()
    WORLD.automod_rules.clear(); WORLD.guild_automod_rules.clear()
    WORLD.stage_instances.clear()
    WORLD.channel_overwrites.clear()
    WORLD.pinned_messages.clear()


@pytest.fixture
def setup():
    _reset_world()
    client = TestClient(create_app())

    # owner + bot + guild
    r = client.post("/admin/users", json={"username": "owner"})
    owner_id = r.json()["user"]["id"]

    r = client.post("/admin/users", json={"username": "bot", "bot": True})
    bot_id = r.json()["user"]["id"]
    token = r.json()["token"]

    r = client.post("/admin/guilds", json={"name": "g", "owner_id": owner_id})
    guild = r.json()
    gid = guild["id"]
    ch_id = guild["channels"][0]["id"]

    client.post(f"/admin/guilds/{gid}/members", json={"user_id": bot_id})

    headers = {"Authorization": f"Bot {token}"}
    return {
        "client": client, "headers": headers, "guild_id": gid,
        "channel_id": ch_id, "bot_id": bot_id, "owner_id": owner_id,
    }


def _entries(client: TestClient, headers: dict, gid: str) -> list[dict]:
    r = client.get(f"/api/v10/guilds/{gid}/audit-logs", headers=headers)
    assert r.status_code == 200
    return r.json()["audit_log_entries"]


def _find(entries: list[dict], action_type: int, target_id: str | None = None) -> dict | None:
    for e in entries:
        if e["action_type"] != action_type:
            continue
        if target_id is not None and e.get("target_id") != target_id:
            continue
        return e
    return None


# --- 1-3: roles ----------------------------------------------------------

def test_role_create_logs_30(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/roles",
        headers=s["headers"], json={"name": "vip"},
    )
    assert r.status_code == 200
    role_id = r.json()["id"]

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e = _find(entries, 30, role_id)
    assert e is not None, f"no ROLE_CREATE entry: {entries}"
    assert e["user_id"] == s["bot_id"]
    assert e["target_id"] == role_id


def test_role_update_logs_31(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/roles",
        headers=s["headers"], json={"name": "vip"},
    )
    role_id = r.json()["id"]

    r = s["client"].patch(
        f"/api/v10/guilds/{s['guild_id']}/roles/{role_id}",
        headers=s["headers"], json={"name": "vip2", "color": 15},
    )
    assert r.status_code == 200

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e = _find(entries, 31, role_id)
    assert e is not None
    assert e["user_id"] == s["bot_id"]
    keys = {c["key"] for c in e["changes"]}
    assert "name" in keys and "color" in keys


def test_role_delete_logs_32(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/roles",
        headers=s["headers"], json={"name": "tmp"},
    )
    role_id = r.json()["id"]

    r = s["client"].delete(
        f"/api/v10/guilds/{s['guild_id']}/roles/{role_id}",
        headers=s["headers"],
    )
    assert r.status_code == 204

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    assert _find(entries, 32, role_id) is not None


# --- 4-5: channels --------------------------------------------------------

def test_channel_create_logs_10(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/channels",
        headers=s["headers"], json={"name": "new-chan", "type": 0},
    )
    assert r.status_code == 201
    cid = r.json()["id"]

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e = _find(entries, 10, cid)
    assert e is not None
    assert e["user_id"] == s["bot_id"]


def test_channel_delete_logs_12(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/channels",
        headers=s["headers"], json={"name": "doomed", "type": 0},
    )
    cid = r.json()["id"]

    r = s["client"].delete(f"/api/v10/channels/{cid}", headers=s["headers"])
    assert r.status_code == 200

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    assert _find(entries, 12, cid) is not None


# --- 6: bans --------------------------------------------------------------

def test_ban_add_remove_logs_22_23(setup):
    s = setup
    # create a victim user + member
    r = s["client"].post("/admin/users", json={"username": "victim"})
    victim_id = r.json()["user"]["id"]
    s["client"].post(
        f"/admin/guilds/{s['guild_id']}/members", json={"user_id": victim_id}
    )

    r = s["client"].put(
        f"/api/v10/guilds/{s['guild_id']}/bans/{victim_id}",
        headers=s["headers"], json={"reason": "spam"},
    )
    assert r.status_code == 204

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e_add = _find(entries, 22, victim_id)
    assert e_add is not None
    assert e_add["reason"] == "spam"
    assert e_add["user_id"] == s["bot_id"]

    r = s["client"].delete(
        f"/api/v10/guilds/{s['guild_id']}/bans/{victim_id}",
        headers=s["headers"],
    )
    assert r.status_code == 204

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    assert _find(entries, 23, victim_id) is not None


# --- 7: emoji -------------------------------------------------------------

def test_emoji_create_logs_60(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/emojis",
        headers=s["headers"],
        json={"name": "happy", "image": "data:image/png;base64,aGk="},
    )
    assert r.status_code == 201
    eid = r.json()["id"]

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e = _find(entries, 60, eid)
    assert e is not None
    assert e["user_id"] == s["bot_id"]


# --- 8: webhook -----------------------------------------------------------

def test_webhook_create_logs_50(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/channels/{s['channel_id']}/webhooks",
        headers=s["headers"], json={"name": "hook"},
    )
    assert r.status_code == 200
    wid = r.json()["id"]

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e = _find(entries, 50, wid)
    assert e is not None
    assert e["user_id"] == s["bot_id"]


# --- 9: invite ------------------------------------------------------------

def test_invite_create_logs_40(setup):
    s = setup
    r = s["client"].post(
        f"/api/v10/channels/{s['channel_id']}/invites",
        headers=s["headers"], json={},
    )
    assert r.status_code == 200
    code = r.json()["code"]

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e = _find(entries, 40)
    assert e is not None
    assert e["user_id"] == s["bot_id"]
    keys = {c["key"]: c.get("new_value") for c in e["changes"]}
    assert keys.get("code") == code


# --- 10: guild patch ------------------------------------------------------

def test_guild_patch_logs_1(setup):
    s = setup
    r = s["client"].patch(
        f"/api/v10/guilds/{s['guild_id']}",
        headers=s["headers"], json={"name": "renamed"},
    )
    assert r.status_code == 200

    entries = _entries(s["client"], s["headers"], s["guild_id"])
    e = _find(entries, 1, s["guild_id"])
    assert e is not None
    assert e["user_id"] == s["bot_id"]
    keys = {c["key"] for c in e["changes"]}
    assert "name" in keys


# --- bonus: filter by action_type works on hookups ----------------------

def test_audit_log_query_filter(setup):
    s = setup
    # create role + channel
    r = s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/roles",
        headers=s["headers"], json={"name": "r1"},
    )
    role_id = r.json()["id"]
    s["client"].post(
        f"/api/v10/guilds/{s['guild_id']}/channels",
        headers=s["headers"], json={"name": "c1"},
    )

    # filter to action_type=30 (ROLE_CREATE) only
    r = s["client"].get(
        f"/api/v10/guilds/{s['guild_id']}/audit-logs?action_type=30",
        headers=s["headers"],
    )
    assert r.status_code == 200
    es = r.json()["audit_log_entries"]
    assert len(es) >= 1
    assert all(e["action_type"] == 30 for e in es)
    assert any(e["target_id"] == role_id for e in es)

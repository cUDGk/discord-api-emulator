"""Slash command end-to-end: register with TestClient, dispatch INTERACTION_CREATE
via admin API, bot's callback is exercised through /interactions/{id}/{token}/callback.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from dapi_emu.app import create_app
from dapi_emu.state import WORLD


def _reset() -> None:
    for attr in ("users", "guilds", "channels", "messages", "channel_messages",
                 "roles", "members", "applications", "bot_tokens",
                 "commands", "guild_commands", "global_commands",
                 "interaction_tokens"):
        try:
            getattr(WORLD, attr).clear()
        except AttributeError:
            pass


def test_slash_registration_and_callback() -> None:
    _reset()
    c = TestClient(create_app())

    owner = c.post("/admin/users", json={"username": "u"}).json()["user"]
    bot = c.post("/admin/users", json={"username": "slashbot", "bot": True}).json()
    token = bot["token"]; bot_id = bot["user"]["id"]; app_id = bot["application_id"]
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    gid = g["id"]; ch_id = g["channels"][0]["id"]
    c.post(f"/admin/guilds/{gid}/members", json={"user_id": bot_id})
    h = {"Authorization": f"Bot {token}"}

    # 1. Register a slash command globally (discord.py tree.sync analog)
    r = c.put(f"/api/v10/applications/{app_id}/commands", headers=h, json=[
        {"name": "hello", "description": "say hi", "type": 1},
    ])
    assert r.status_code < 300, r.text
    cmds = r.json()
    assert any(cmd["name"] == "hello" for cmd in cmds)
    cmd_id = cmds[0]["id"]

    # 2. Simulate a user invoking /hello via admin dispatch + seed interaction token
    itok = "ci_test_interaction_token"
    seed = c.post("/admin/interaction-tokens", json={
        "token": itok,
        "interaction_id": "77770000",
        "interaction_type": 2,
        "application_id": app_id,
        "channel_id": ch_id,
        "guild_id": gid,
        "user_id": owner["id"],
    })
    assert seed.status_code == 200, seed.text

    # 3. Bot side: respond to the interaction (type=4 CHANNEL_MESSAGE_WITH_SOURCE)
    r = c.post(f"/api/v10/interactions/77770000/{itok}/callback", json={
        "type": 4,
        "data": {"content": "Hi owner!"},
    })
    assert r.status_code < 300, r.text

    # 4. Verify the channel now contains the interaction response message
    messages = c.get(f"/admin/channels/{ch_id}/messages").json()
    contents = [m["content"] for m in messages]
    assert "Hi owner!" in contents, contents

    # 5. Followup via /webhooks/{app_id}/{interaction_token}
    r = c.post(f"/api/v10/webhooks/{app_id}/{itok}", json={
        "content": "followup too",
    })
    assert r.status_code < 300, r.text
    # Edit @original
    r = c.patch(f"/api/v10/webhooks/{app_id}/{itok}/messages/@original",
                json={"content": "Hi owner (edited)"})
    assert r.status_code < 300, r.text

    # 6. Confirm state
    messages = c.get(f"/admin/channels/{ch_id}/messages").json()
    contents = [m["content"] for m in messages]
    assert "followup too" in contents
    assert "Hi owner (edited)" in contents, contents


def test_deferred_response_then_edit() -> None:
    """type=5 (DEFERRED) creates a loading message; editing @original clears LOADING bit."""
    _reset()
    c = TestClient(create_app())

    bot = c.post("/admin/users", json={"username": "b", "bot": True}).json()
    owner = c.post("/admin/users", json={"username": "o"}).json()["user"]
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    # silent join — keep the channel free of system messages so we can
    # assert on the loading message at messages[-1].
    c.post(f"/admin/guilds/{g['id']}/members",
           json={"user_id": bot["user"]["id"], "silent": True})

    itok = "deferred_tok"
    c.post("/admin/interaction-tokens", json={
        "token": itok, "interaction_id": "1",
        "application_id": bot["application_id"],
        "channel_id": g["channels"][0]["id"], "guild_id": g["id"],
        "user_id": owner["id"],
    })
    # Deferred response
    r = c.post(f"/api/v10/interactions/1/{itok}/callback",
               json={"type": 5})
    assert r.status_code < 300, r.text

    # LOADING bit should be set on the most recent message (the loading one)
    messages = c.get(f"/admin/channels/{g['channels'][0]['id']}/messages").json()
    assert messages
    loading_msg = messages[-1]
    assert loading_msg["flags"] & 128, "LOADING flag missing"

    # Edit @original -> clears LOADING
    r = c.patch(f"/api/v10/webhooks/{bot['application_id']}/{itok}/messages/@original",
                json={"content": "actual answer"})
    assert r.status_code < 300, r.text
    messages = c.get(f"/admin/channels/{g['channels'][0]['id']}/messages").json()
    edited = next(m for m in messages if m["id"] == loading_msg["id"])
    assert edited["content"] == "actual answer"
    assert not (edited["flags"] & 128), "LOADING still set"

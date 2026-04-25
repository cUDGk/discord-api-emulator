"""イベント連動のシステムメッセージ自動投稿を検証。"""
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
                 "threads", "thread_members", "pinned_messages",
                 "stage_instances"):
        getattr(WORLD, attr).clear()


def _msgs(c, ch_id):
    return c.get(f"/admin/channels/{ch_id}/messages").json()


def _types(c, ch_id):
    return [m["type"] for m in _msgs(c, ch_id)]


def test_user_join_posts_type_7_in_system_channel() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "owner"}).json()["user"]
    new = c.post("/admin/users", json={"username": "newcomer"}).json()["user"]
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    ch = g["channels"][0]["id"]
    # system_channel_id should default to first text channel
    assert g["system_channel_id"] == ch

    # owner already member; the create_guild path doesn't auto-post a join
    # (legacy behaviour). Add another member -> USER_JOIN.
    c.post(f"/admin/guilds/{g['id']}/members", json={"user_id": new["id"]})
    types = _types(c, ch)
    assert 7 in types, f"USER_JOIN not posted; got types={types}"


def test_silent_join_skips_system_message() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "owner"}).json()["user"]
    new = c.post("/admin/users", json={"username": "stealth"}).json()["user"]
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    ch = g["channels"][0]["id"]
    c.post(f"/admin/guilds/{g['id']}/members", json={"user_id": new["id"], "silent": True})
    assert 7 not in _types(c, ch)


def test_pin_posts_type_6() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "o"}).json()["user"]
    bot = c.post("/admin/users", json={"username": "b", "bot": True}).json()
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    c.post(f"/admin/guilds/{g['id']}/members", json={"user_id": bot["user"]["id"]})
    ch = g["channels"][0]["id"]
    h = {"Authorization": f"Bot {bot['token']}"}

    msg_id = c.post(f"/api/v10/channels/{ch}/messages", headers=h,
                    json={"content": "pin me"}).json()["id"]
    c.put(f"/api/v10/channels/{ch}/pins/{msg_id}", headers=h)
    types = _types(c, ch)
    assert 6 in types, f"CHANNEL_PINNED_MESSAGE not posted; types={types}"


def test_thread_create_posts_type_18_in_parent() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "o"}).json()["user"]
    bot = c.post("/admin/users", json={"username": "b", "bot": True}).json()
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    c.post(f"/admin/guilds/{g['id']}/members", json={"user_id": bot["user"]["id"]})
    ch = g["channels"][0]["id"]
    h = {"Authorization": f"Bot {bot['token']}"}

    th = c.post(f"/api/v10/channels/{ch}/threads", headers=h,
                json={"name": "test-thread", "type": 11}).json()
    assert th.get("id")
    types = _types(c, ch)
    assert 18 in types, f"THREAD_CREATED not posted; types={types}"


def test_reply_message_has_type_19() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "o"}).json()["user"]
    bot = c.post("/admin/users", json={"username": "b", "bot": True}).json()
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    c.post(f"/admin/guilds/{g['id']}/members", json={"user_id": bot["user"]["id"]})
    ch = g["channels"][0]["id"]
    h = {"Authorization": f"Bot {bot['token']}"}

    parent = c.post(f"/api/v10/channels/{ch}/messages", headers=h,
                    json={"content": "first"}).json()
    reply = c.post(f"/api/v10/channels/{ch}/messages", headers=h, json={
        "content": "reply",
        "message_reference": {"message_id": parent["id"], "channel_id": ch},
    }).json()
    assert reply["type"] == 19, f"expected REPLY (19), got {reply['type']}"


def test_boost_simulator() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "o"}).json()["user"]
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    ch = g["channels"][0]["id"]
    r = c.post(f"/admin/guilds/{g['id']}/boost", json={"user_id": owner["id"], "tier": 0})
    assert r.status_code == 200
    types = _types(c, ch)
    assert 8 in types  # GUILD_BOOST


def test_admin_post_arbitrary_system_message() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "o"}).json()["user"]
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    ch = g["channels"][0]["id"]
    r = c.post("/admin/system-message", json={
        "channel_id": ch, "type": 24, "content": "automod note",
    })
    assert r.status_code == 200
    types = _types(c, ch)
    assert 24 in types  # AUTO_MODERATION_ACTION


def test_call_simulator_in_dm() -> None:
    _reset()
    c = TestClient(create_app())
    bot = c.post("/admin/users", json={"username": "b", "bot": True}).json()
    alice = c.post("/admin/users", json={"username": "alice"}).json()["user"]
    h = {"Authorization": f"Bot {bot['token']}"}
    dm = c.post("/api/v10/users/@me/channels", headers=h,
                json={"recipient_id": alice["id"]}).json()
    r = c.post(f"/admin/channels/{dm['id']}/call", json={"user_id": bot["user"]["id"]})
    assert r.status_code == 200
    assert 3 in _types(c, dm["id"])


def test_stage_start_and_end_post_system_messages() -> None:
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "o"}).json()["user"]
    bot = c.post("/admin/users", json={"username": "b", "bot": True}).json()
    g = c.post("/admin/guilds", json={"name": "G", "owner_id": owner["id"]}).json()
    c.post(f"/admin/guilds/{g['id']}/members", json={"user_id": bot["user"]["id"]})
    # Create stage channel (type 13)
    stg = c.post(f"/admin/guilds/{g['id']}/channels", json={"name": "stage", "type": 13}).json()
    h = {"Authorization": f"Bot {bot['token']}"}
    c.post("/api/v10/stage-instances", headers=h,
           json={"channel_id": stg["id"], "topic": "hi", "privacy_level": 2})
    types = _types(c, stg["id"])
    assert 27 in types  # STAGE_START
    c.delete(f"/api/v10/stage-instances/{stg['id']}", headers=h)
    types = _types(c, stg["id"])
    assert 28 in types  # STAGE_END

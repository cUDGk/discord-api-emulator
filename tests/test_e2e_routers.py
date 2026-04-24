"""End-to-end smoke of every router's main endpoints against a live server.

Run: `pytest tests/test_e2e_routers.py -v` with the emulator on 127.0.0.1:8080.

The point is to flush out 500s and shape errors — not to validate Discord
protocol correctness. A 2xx response is considered OK.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

# Allow importing dapi_emu for direct state manipulation where needed.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

BASE = os.environ.get("DAPI_BASE", "http://127.0.0.1:8080")
API = f"{BASE}/api/v10"


def _ok(status: int) -> bool:
    return 200 <= status < 300


@pytest.fixture(scope="module")
def session():
    """Spin up a fresh world: owner, bot, guild with default #general, bot as member."""
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        r = c.post("/admin/reset")
        assert r.status_code == 200, f"/admin/reset failed: {r.status_code} {r.text}"

        r = c.post("/admin/users", json={"username": "owner"})
        assert r.status_code == 200, r.text
        owner = r.json()

        r = c.post("/admin/users", json={"username": "bot", "bot": True})
        assert r.status_code == 200, r.text
        bot = r.json()

        r = c.post(
            "/admin/guilds",
            json={"name": "TestGuild", "owner_id": owner["user"]["id"]},
        )
        assert r.status_code == 200, r.text
        guild = r.json()

        r = c.post(
            f"/admin/guilds/{guild['id']}/members",
            json={"user_id": bot["user"]["id"]},
        )
        assert r.status_code == 200, r.text

        # Also add owner message so later poll/pin tests have something to target.
        r = c.post(
            "/admin/send-message",
            json={
                "author_id": owner["user"]["id"],
                "channel_id": guild["channels"][0]["id"],
                "content": "hello",
            },
        )
        assert r.status_code == 200, r.text
        seed_msg = r.json()

        yield {
            "base": BASE,
            "api": API,
            "token": bot["token"],
            "bot_id": bot["user"]["id"],
            "app_id": bot["application_id"],
            "owner_id": owner["user"]["id"],
            "guild_id": guild["id"],
            "channel_id": guild["channels"][0]["id"],
            "seed_msg_id": seed_msg["id"],
            "headers": {"Authorization": f"Bot {bot['token']}"},
        }


@pytest.fixture
def client(session):
    headers = session["headers"]
    with httpx.Client(base_url=session["api"], headers=headers, timeout=10.0) as c:
        yield c


# =====================================================================
# 1. Interactions (application commands + callback)
# =====================================================================

def test_create_global_command(client, session):
    r = client.post(
        f"/applications/{session['app_id']}/commands",
        json={"name": "ping", "description": "pong", "type": 1},
    )
    assert _ok(r.status_code), f"{r.status_code} {r.text}"
    session["global_cmd_id"] = r.json()["id"]


def test_list_global_commands(client, session):
    r = client.get(f"/applications/{session['app_id']}/commands")
    assert _ok(r.status_code), r.text
    assert isinstance(r.json(), list)


def test_edit_global_command(client, session):
    cid = session["global_cmd_id"]
    r = client.patch(
        f"/applications/{session['app_id']}/commands/{cid}",
        json={"description": "new desc"},
    )
    assert _ok(r.status_code), r.text


def test_delete_global_command(client, session):
    cid = session["global_cmd_id"]
    r = client.delete(f"/applications/{session['app_id']}/commands/{cid}")
    assert _ok(r.status_code), r.text


def test_bulk_overwrite_guild_commands(client, session):
    r = client.put(
        f"/applications/{session['app_id']}/guilds/{session['guild_id']}/commands",
        json=[{"name": "hello", "description": "hi", "type": 1}],
    )
    assert _ok(r.status_code), r.text


def test_interaction_callback(client, session):
    """Seed an interaction token via admin endpoint, then POST type=4 callback."""
    token = "itok_test_e2e"
    seed = httpx.post(f"{BASE}/admin/interaction-tokens", json={
        "token": token,
        "interaction_id": "99990000",
        "interaction_type": 2,
        "application_id": session["app_id"],
        "channel_id": session["channel_id"],
        "user_id": session["owner_id"],
    })
    assert seed.status_code == 200, seed.text
    r = client.post(
        f"/interactions/99990000/{token}/callback",
        json={"type": 4, "data": {"content": "interaction body"}},
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 2. Threads
# =====================================================================

def test_create_thread(client, session):
    r = client.post(
        f"/channels/{session['channel_id']}/threads",
        json={"name": "a thread", "type": 11, "auto_archive_duration": 60},
    )
    assert _ok(r.status_code), r.text
    session["thread_id"] = r.json()["id"]


def test_add_thread_member(client, session):
    tid = session["thread_id"]
    r = client.put(f"/channels/{tid}/thread-members/{session['owner_id']}")
    assert _ok(r.status_code), r.text


def test_remove_thread_member(client, session):
    tid = session["thread_id"]
    r = client.delete(f"/channels/{tid}/thread-members/{session['owner_id']}")
    assert _ok(r.status_code), r.text


def test_list_archived_public(client, session):
    r = client.get(f"/channels/{session['channel_id']}/threads/archived/public")
    assert _ok(r.status_code), r.text


def test_active_guild_threads(client, session):
    r = client.get(f"/guilds/{session['guild_id']}/threads/active")
    assert _ok(r.status_code), r.text


# =====================================================================
# 3. Webhooks
# =====================================================================

def test_create_webhook(client, session):
    r = client.post(
        f"/channels/{session['channel_id']}/webhooks",
        json={"name": "TestHook"},
    )
    assert _ok(r.status_code), r.text
    wh = r.json()
    session["webhook_id"] = wh["id"]
    session["webhook_token"] = wh["token"]


def test_execute_webhook(client, session):
    wid = session["webhook_id"]
    tok = session["webhook_token"]
    # Execute uses token path — no Bot auth needed, but our client sends it anyway.
    r = client.post(
        f"/webhooks/{wid}/{tok}?wait=true",
        json={"content": "webhook says hi"},
    )
    assert _ok(r.status_code), r.text
    session["webhook_msg_id"] = r.json()["id"]


def test_edit_webhook(client, session):
    wid = session["webhook_id"]
    r = client.patch(f"/webhooks/{wid}", json={"name": "TestHookRenamed"})
    assert _ok(r.status_code), r.text


def test_delete_webhook(client, session):
    wid = session["webhook_id"]
    r = client.delete(f"/webhooks/{wid}")
    assert _ok(r.status_code), r.text


# =====================================================================
# 4. DM
# =====================================================================

def test_create_dm(client, session):
    r = client.post("/users/@me/channels", json={"recipient_id": session["owner_id"]})
    assert _ok(r.status_code), r.text
    session["dm_channel_id"] = r.json()["id"]


def test_list_my_dms(client, session):
    r = client.get("/users/@me/channels")
    assert _ok(r.status_code), r.text


# =====================================================================
# 5. Pins
# =====================================================================

def test_create_message_for_pin(client, session):
    r = client.post(
        f"/channels/{session['channel_id']}/messages",
        json={"content": "pin me"},
    )
    assert _ok(r.status_code), r.text
    session["pin_msg_id"] = r.json()["id"]


def test_pin_message(client, session):
    r = client.put(
        f"/channels/{session['channel_id']}/pins/{session['pin_msg_id']}",
    )
    assert _ok(r.status_code), r.text


def test_list_pins(client, session):
    r = client.get(f"/channels/{session['channel_id']}/pins")
    assert _ok(r.status_code), r.text


# =====================================================================
# 6. Bulk delete
# =====================================================================

def test_bulk_delete(client, session):
    # Create 3 messages then bulk-delete them.
    ids = []
    for i in range(3):
        r = client.post(
            f"/channels/{session['channel_id']}/messages",
            json={"content": f"bulk {i}"},
        )
        assert _ok(r.status_code), r.text
        ids.append(r.json()["id"])
    r = client.post(
        f"/channels/{session['channel_id']}/messages/bulk-delete",
        json={"messages": ids},
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 7. Emojis
# =====================================================================

_TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def test_create_guild_emoji(client, session):
    r = client.post(
        f"/guilds/{session['guild_id']}/emojis",
        json={"name": "pogchamp", "image": _TINY_PNG_DATA_URL, "roles": []},
    )
    assert _ok(r.status_code), r.text
    session["emoji_id"] = r.json()["id"]


def test_list_guild_emojis(client, session):
    r = client.get(f"/guilds/{session['guild_id']}/emojis")
    assert _ok(r.status_code), r.text


def test_edit_guild_emoji(client, session):
    r = client.patch(
        f"/guilds/{session['guild_id']}/emojis/{session['emoji_id']}",
        json={"name": "pog2"},
    )
    assert _ok(r.status_code), r.text


def test_delete_guild_emoji(client, session):
    r = client.delete(
        f"/guilds/{session['guild_id']}/emojis/{session['emoji_id']}",
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 8. Stickers
# =====================================================================

def test_create_guild_sticker(client, session):
    r = client.post(
        f"/guilds/{session['guild_id']}/stickers",
        json={"name": "mysticker", "tags": "fun", "description": "test"},
    )
    assert _ok(r.status_code), r.text
    session["sticker_id"] = r.json()["id"]


def test_list_guild_stickers(client, session):
    r = client.get(f"/guilds/{session['guild_id']}/stickers")
    assert _ok(r.status_code), r.text


def test_get_sticker_packs(client, session):
    r = client.get("/sticker-packs")
    assert _ok(r.status_code), r.text


def test_delete_guild_sticker(client, session):
    r = client.delete(
        f"/guilds/{session['guild_id']}/stickers/{session['sticker_id']}",
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 9. Invites
# =====================================================================

def test_create_invite(client, session):
    r = client.post(
        f"/channels/{session['channel_id']}/invites",
        json={"max_age": 3600, "max_uses": 0},
    )
    assert _ok(r.status_code), r.text
    session["invite_code"] = r.json()["code"]


def test_get_invite(client, session):
    r = client.get(f"/invites/{session['invite_code']}")
    assert _ok(r.status_code), r.text


def test_delete_invite(client, session):
    r = client.delete(f"/invites/{session['invite_code']}")
    assert _ok(r.status_code), r.text


# =====================================================================
# 10. Bans
# =====================================================================

def test_ban_flow(client, session):
    # Create a user to ban.
    with httpx.Client(base_url=session["base"], timeout=10.0) as admin:
        r = admin.post("/admin/users", json={"username": "banned"})
        banned_id = r.json()["user"]["id"]
        admin.post(
            f"/admin/guilds/{session['guild_id']}/members",
            json={"user_id": banned_id},
        )

    r = client.put(
        f"/guilds/{session['guild_id']}/bans/{banned_id}",
        json={"reason": "test"},
    )
    assert _ok(r.status_code), r.text

    r = client.get(f"/guilds/{session['guild_id']}/bans")
    assert _ok(r.status_code), r.text

    r = client.delete(f"/guilds/{session['guild_id']}/bans/{banned_id}")
    assert _ok(r.status_code), r.text


# =====================================================================
# 11. Audit log
# =====================================================================

def test_audit_log(client, session):
    r = client.get(f"/guilds/{session['guild_id']}/audit-logs")
    assert _ok(r.status_code), r.text


# =====================================================================
# 12. Voice
# =====================================================================

def test_voice_regions(client, session):
    r = client.get("/voice/regions")
    assert _ok(r.status_code), r.text


def test_guild_voice_regions(client, session):
    r = client.get(f"/guilds/{session['guild_id']}/voice-regions")
    assert _ok(r.status_code), r.text


def test_edit_voice_state(client, session):
    # No channel_id change — just touch the endpoint.
    r = client.patch(
        f"/guilds/{session['guild_id']}/voice-states/@me",
        json={"suppress": False},
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 13. Stage instances
# =====================================================================

def test_stage_instance_flow(client, session):
    # Need a stage channel (type=13).
    with httpx.Client(base_url=session["base"], timeout=10.0) as admin:
        r = admin.post(
            f"/admin/guilds/{session['guild_id']}/channels",
            json={"name": "stage", "type": 13},
        )
        assert r.status_code == 200, r.text
        stage_channel_id = r.json()["id"]

    r = client.post(
        "/stage-instances",
        json={"channel_id": stage_channel_id, "topic": "Hello"},
    )
    assert _ok(r.status_code), r.text

    r = client.get(f"/stage-instances/{stage_channel_id}")
    assert _ok(r.status_code), r.text

    r = client.delete(f"/stage-instances/{stage_channel_id}")
    assert _ok(r.status_code), r.text


# =====================================================================
# 14. Scheduled events
# =====================================================================

def test_scheduled_event_flow(client, session):
    r = client.post(
        f"/guilds/{session['guild_id']}/scheduled-events",
        json={
            "name": "My Event",
            "scheduled_start_time": "2099-01-01T00:00:00+00:00",
            "entity_type": 3,
            "privacy_level": 2,
            "entity_metadata": {"location": "here"},
        },
    )
    assert _ok(r.status_code), r.text
    event_id = r.json()["id"]

    r = client.get(f"/guilds/{session['guild_id']}/scheduled-events/{event_id}")
    assert _ok(r.status_code), r.text

    r = client.patch(
        f"/guilds/{session['guild_id']}/scheduled-events/{event_id}",
        json={"name": "Renamed"},
    )
    assert _ok(r.status_code), r.text

    r = client.delete(f"/guilds/{session['guild_id']}/scheduled-events/{event_id}")
    assert _ok(r.status_code), r.text


# =====================================================================
# 15. AutoMod
# =====================================================================

def test_automod_flow(client, session):
    r = client.post(
        f"/guilds/{session['guild_id']}/auto-moderation/rules",
        json={
            "name": "nobad",
            "event_type": 1,
            "trigger_type": 1,
            "actions": [{"type": 1}],
            "enabled": True,
        },
    )
    assert _ok(r.status_code), r.text
    rule_id = r.json()["id"]

    r = client.get(f"/guilds/{session['guild_id']}/auto-moderation/rules")
    assert _ok(r.status_code), r.text

    r = client.patch(
        f"/guilds/{session['guild_id']}/auto-moderation/rules/{rule_id}",
        json={"enabled": False},
    )
    assert _ok(r.status_code), r.text

    r = client.delete(
        f"/guilds/{session['guild_id']}/auto-moderation/rules/{rule_id}",
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 16. Polls
# =====================================================================

def test_poll_endpoints(client, session):
    """Seed a poll via admin endpoint for a real message, then hit poll REST."""
    r = client.post(
        f"/channels/{session['channel_id']}/messages",
        json={"content": "poll msg"},
    )
    assert _ok(r.status_code), r.text
    msg_id = r.json()["id"]

    seed = httpx.put(f"{BASE}/admin/polls/{msg_id}", json={
        "question": {"text": "Which?"},
        "answers": [
            {"answer_id": 1, "poll_media": {"text": "A"}},
            {"answer_id": 2, "poll_media": {"text": "B"}},
        ],
        "allow_multiselect": False,
        "layout_type": 1,
        "results": {"is_finalized": False, "answer_counts": []},
    })
    assert seed.status_code == 200, seed.text

    r = client.get(
        f"/channels/{session['channel_id']}/polls/{msg_id}/answers/1",
    )
    assert _ok(r.status_code), r.text

    r = client.post(
        f"/channels/{session['channel_id']}/polls/{msg_id}/expire",
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 17. OAuth2
# =====================================================================

def test_oauth2_connections(client, session):
    r = client.get("/users/@me/connections")
    assert _ok(r.status_code), r.text


def test_oauth2_me(session):
    """/oauth2/@me uses a Bearer token — issue one first via client_credentials."""
    with httpx.Client(base_url=session["api"], timeout=10.0) as c:
        r = c.post(
            "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": session["app_id"],
                "scope": "identify",
            },
        )
        assert _ok(r.status_code), r.text
        bearer = r.json()["access_token"]

        r = c.get("/oauth2/@me", headers={"Authorization": f"Bearer {bearer}"})
        assert _ok(r.status_code), r.text


# =====================================================================
# 18. CDN
# =====================================================================

def test_cdn_avatar(session):
    """CDN is mounted under /api/v10 — per app.py it's included in prefix loop."""
    with httpx.Client(base_url=session["api"], timeout=10.0) as c:
        r = c.get("/cdn/avatars/123/abc.png")
        assert _ok(r.status_code), r.text
        assert r.headers.get("content-type", "").startswith("image/"), r.headers


# =====================================================================
# 19. Entitlements
# =====================================================================

def test_entitlements_flow(client, session):
    r = client.post(
        f"/applications/{session['app_id']}/entitlements",
        json={
            "sku_id": "987654321",
            "owner_id": session["owner_id"],
            "owner_type": 2,  # user
        },
    )
    assert _ok(r.status_code), r.text
    ent_id = r.json()["id"]

    r = client.get(f"/applications/{session['app_id']}/entitlements")
    assert _ok(r.status_code), r.text

    r = client.delete(
        f"/applications/{session['app_id']}/entitlements/{ent_id}",
    )
    assert _ok(r.status_code), r.text


# =====================================================================
# 20. SKUs
# =====================================================================

def test_list_skus(client, session):
    r = client.get(f"/applications/{session['app_id']}/skus")
    assert _ok(r.status_code), r.text


# =====================================================================
# 21. Teams
# =====================================================================

def test_get_team(client, session):
    """Seed a team via admin endpoint since there's no Discord-side create."""
    team_id = "700000000000000001"
    seed = httpx.put(f"{BASE}/admin/teams/{team_id}", json={
        "name": "The Team",
        "owner_user_id": session["owner_id"],
        "members": [{"user_id": session["owner_id"], "membership_state": 2, "role": "owner"}],
    })
    assert seed.status_code == 200, seed.text
    r = client.get(f"/teams/{team_id}")
    assert _ok(r.status_code), r.text


# =====================================================================
# 22. Roles (guilds.py)
# =====================================================================

def test_role_flow(client, session):
    r = client.post(
        f"/guilds/{session['guild_id']}/roles",
        json={"name": "Mods", "color": 123, "permissions": "0"},
    )
    assert _ok(r.status_code), r.text
    rid = r.json()["id"]

    r = client.patch(
        f"/guilds/{session['guild_id']}/roles/{rid}",
        json={"name": "Mods2"},
    )
    assert _ok(r.status_code), r.text

    r = client.delete(f"/guilds/{session['guild_id']}/roles/{rid}")
    assert _ok(r.status_code), r.text


# =====================================================================
# 23. Messages (channels.py) — edit / delete / reaction
# =====================================================================

def test_message_edit_delete_reaction(client, session):
    # Create a message as the bot.
    r = client.post(
        f"/channels/{session['channel_id']}/messages",
        json={"content": "hi"},
    )
    assert _ok(r.status_code), r.text
    mid = r.json()["id"]

    # Edit
    r = client.patch(
        f"/channels/{session['channel_id']}/messages/{mid}",
        json={"content": "edited"},
    )
    assert _ok(r.status_code), r.text

    # Reaction add + remove
    r = client.put(
        f"/channels/{session['channel_id']}/messages/{mid}/reactions/%F0%9F%91%8D/@me",
    )
    assert _ok(r.status_code), r.text

    r = client.delete(
        f"/channels/{session['channel_id']}/messages/{mid}/reactions/%F0%9F%91%8D/@me",
    )
    assert _ok(r.status_code), r.text

    # Delete
    r = client.delete(f"/channels/{session['channel_id']}/messages/{mid}")
    assert _ok(r.status_code), r.text

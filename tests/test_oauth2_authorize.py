"""Tests for the browser-facing OAuth2 authorize HTML flow."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from dapi_emu.app import create_app
from dapi_emu.state import WORLD


def _fresh_client() -> TestClient:
    WORLD.users.clear(); WORLD.guilds.clear(); WORLD.channels.clear()
    WORLD.messages.clear(); WORLD.channel_messages.clear()
    WORLD.roles.clear(); WORLD.members.clear()
    WORLD.applications.clear(); WORLD.bot_tokens.clear()
    WORLD.oauth_authorizations.clear(); WORLD.oauth_tokens.clear()
    return TestClient(create_app())


def _setup_bot_and_user_and_guild(c: TestClient) -> tuple[str, str, str, str]:
    """Create bot+app, regular user, and guild. Returns (app_id, bot_id, user_id, guild_id)."""
    r = c.post("/admin/users", json={"username": "mybot", "bot": True})
    assert r.status_code == 200
    bot_id = r.json()["user"]["id"]
    app_id = r.json()["application_id"]

    r = c.post("/admin/users", json={"username": "alice"})
    assert r.status_code == 200
    user_id = r.json()["user"]["id"]

    r = c.post("/admin/guilds", json={"name": "test-guild", "owner_id": user_id})
    assert r.status_code == 200
    guild_id = r.json()["id"]

    return app_id, bot_id, user_id, guild_id


def test_authorize_get_renders_html_with_selectors():
    c = _fresh_client()
    app_id, _bot_id, _user_id, _guild_id = _setup_bot_and_user_and_guild(c)

    r = c.get(
        "/api/v10/oauth2/authorize",
        params={
            "client_id": app_id,
            "redirect_uri": "http://localhost/cb",
            "response_type": "code",
            "scope": "bot applications.commands",
            "state": "xyz",
        },
    )
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    body = r.text

    # App name is rendered.
    assert "mybot" in body
    # Both selector types are present.
    assert 'name="user_id"' in body
    assert 'name="guild_id"' in body
    # Bot and application.commands scopes rendered.
    assert "applications.commands" in body
    # Regular (non-bot) user is listed as a selectable user.
    assert "alice" in body
    # Our guild is listed.
    assert "test-guild" in body
    # Hidden fields for redirect/state/scope preserved.
    assert 'value="http://localhost/cb"' in body
    assert 'value="xyz"' in body


def test_authorize_get_without_bot_scope_omits_guild_selector():
    c = _fresh_client()
    app_id, _bot_id, _user_id, _guild_id = _setup_bot_and_user_and_guild(c)

    r = c.get(
        "/api/v10/oauth2/authorize",
        params={
            "client_id": app_id,
            "redirect_uri": "http://localhost/cb",
            "response_type": "code",
            "scope": "identify",
        },
    )
    assert r.status_code == 200
    body = r.text
    assert 'name="user_id"' in body
    assert 'name="guild_id"' not in body


def test_authorize_get_unknown_client_returns_404():
    c = _fresh_client()
    r = c.get(
        "/api/v10/oauth2/authorize",
        params={
            "client_id": "0000000000",
            "redirect_uri": "http://localhost/cb",
            "response_type": "code",
            "scope": "bot",
        },
    )
    assert r.status_code == 404


def test_authorize_confirm_approve_redirects_with_code_and_adds_bot_to_guild():
    c = _fresh_client()
    app_id, bot_id, user_id, guild_id = _setup_bot_and_user_and_guild(c)

    # Pre-condition: bot is NOT in the guild.
    assert (bot_id, guild_id) not in WORLD.members

    r = c.post(
        "/api/v10/oauth2/authorize/confirm",
        data={
            "client_id": app_id,
            "redirect_uri": "http://localhost/cb",
            "scope": "bot applications.commands",
            "state": "xyz",
            "user_id": user_id,
            "guild_id": guild_id,
            "action": "approve",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "http"
    assert parsed.netloc == "localhost"
    assert parsed.path == "/cb"
    qs = parse_qs(parsed.query)
    assert "code" in qs and qs["code"][0]
    assert qs["state"] == ["xyz"]

    # Bot is now a member of the guild.
    assert (bot_id, guild_id) in WORLD.members

    # The code is a valid issued authorization.
    code = qs["code"][0]
    assert code in WORLD.oauth_authorizations

    # Token exchange using that code succeeds.
    r = c.post(
        "/api/v10/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost/cb",
            "client_id": app_id,
            "client_secret": "dummy",
        },
    )
    assert r.status_code == 200
    tok = r.json()
    assert tok["token_type"] == "Bearer"
    assert tok["access_token"]
    # Scope preserved from the authorization.
    assert "bot" in tok["scope"].split()


def test_authorize_confirm_deny_redirects_with_error():
    c = _fresh_client()
    app_id, _bot_id, user_id, guild_id = _setup_bot_and_user_and_guild(c)

    r = c.post(
        "/api/v10/oauth2/authorize/confirm",
        data={
            "client_id": app_id,
            "redirect_uri": "http://localhost/cb?already=1",
            "scope": "bot",
            "state": "s1",
            "user_id": user_id,
            "guild_id": guild_id,
            "action": "deny",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    parsed = urlparse(r.headers["location"])
    qs = parse_qs(parsed.query)
    assert qs["error"] == ["access_denied"]
    assert qs["state"] == ["s1"]
    # The pre-existing query param is preserved.
    assert qs["already"] == ["1"]


def test_authorize_confirm_without_bot_scope_does_not_touch_members():
    c = _fresh_client()
    app_id, bot_id, user_id, guild_id = _setup_bot_and_user_and_guild(c)

    r = c.post(
        "/api/v10/oauth2/authorize/confirm",
        data={
            "client_id": app_id,
            "redirect_uri": "http://localhost/cb",
            "scope": "identify",
            "user_id": user_id,
            "guild_id": guild_id,  # present but ignored since scope has no 'bot'
            "action": "approve",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    # Bot was NOT added because 'bot' scope wasn't requested.
    assert (bot_id, guild_id) not in WORLD.members

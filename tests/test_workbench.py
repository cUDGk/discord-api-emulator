"""Interaction Workbench — invoke / save / replay testcases."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dapi_emu.app import create_app
from dapi_emu.state import WORLD
from dapi_emu.workbench import runner


@pytest.fixture(autouse=True)
def tc_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DAPI_TESTCASES_DIR", str(tmp_path))
    yield tmp_path


def _reset() -> None:
    for attr in ("users", "guilds", "channels", "messages", "channel_messages",
                 "roles", "members", "applications", "bot_tokens",
                 "commands", "guild_commands", "global_commands",
                 "interaction_tokens"):
        getattr(WORLD, attr).clear()


def _setup_world():
    """Bootstrap: owner + bot + guild + register a single slash command."""
    _reset()
    c = TestClient(create_app())
    owner = c.post("/admin/users", json={"username": "owner"}).json()["user"]
    bot = c.post("/admin/users", json={"username": "wbbot", "bot": True}).json()
    g = c.post("/admin/guilds", json={"name": "WB", "owner_id": owner["id"]}).json()
    c.post(f"/admin/guilds/{g['id']}/members",
           json={"user_id": bot["user"]["id"], "silent": True})
    ch_id = g["channels"][0]["id"]
    h = {"Authorization": f"Bot {bot['token']}"}
    c.put(f"/api/v10/applications/{bot['application_id']}/commands", headers=h,
          json=[{"name": "ping", "description": "pong", "type": 1,
                 "options": [{"type": 3, "name": "arg",
                              "description": "what to echo", "required": False}]}])
    return c, {
        "owner_id": owner["id"],
        "bot": bot,
        "app_id": bot["application_id"],
        "guild_id": g["id"],
        "channel_id": ch_id,
        "headers": h,
    }


def test_inventory_exposes_commands_users_guilds() -> None:
    c, ctx = _setup_world()
    inv = c.get("/workbench/inventory").json()
    assert any(a["id"] == ctx["app_id"] for a in inv["applications"])
    assert any(u["username"] == "owner" for u in inv["users"])
    assert any(g["id"] == ctx["guild_id"] for g in inv["guilds"])
    assert any(cmd["name"] == "ping" for cmd in inv["commands"])


def test_commands_filtered_by_app() -> None:
    c, ctx = _setup_world()
    cmds = c.get(f"/workbench/commands?application_id={ctx['app_id']}").json()
    assert len(cmds) >= 1
    assert all(cmd["application_id"] == ctx["app_id"] for cmd in cmds)
    # unknown app -> empty
    assert c.get("/workbench/commands?application_id=999999").json() == []


def test_invoke_registers_interaction_token_and_publishes() -> None:
    c, ctx = _setup_world()
    r = c.post("/workbench/invoke", json={
        "kind": "application_command",
        "application_id": ctx["app_id"],
        "user_id": ctx["owner_id"],
        "channel_id": ctx["channel_id"],
        "guild_id": ctx["guild_id"],
        "invocation": {
            "command_name": "ping",
            "options": [{"name": "arg", "type": 3, "value": "hello"}],
        },
        "timeout": 0.3,  # short, no bot listening
    })
    assert r.status_code == 200
    body = r.json()
    token = body["payload"]["token"]
    assert token in WORLD.interaction_tokens
    assert WORLD.interaction_tokens[token]["channel_id"] == ctx["channel_id"]
    # No bot is wired to actually respond — the captured response is empty.
    assert body["original_message"] is None


def test_invoke_then_simulate_bot_callback_returns_response() -> None:
    """If something does call /interactions/{}/{token}/callback during the
    poll window, collect_response should pick up the message."""
    c, ctx = _setup_world()
    # Invoke with longer timeout, then synchronously call back in this same
    # process (TestClient is sync — but the dispatch+poll happens entirely
    # in-process, so we can directly POST the callback before timeout).
    r = c.post("/workbench/invoke", json={
        "kind": "application_command",
        "application_id": ctx["app_id"],
        "user_id": ctx["owner_id"],
        "channel_id": ctx["channel_id"],
        "guild_id": ctx["guild_id"],
        "invocation": {"command_name": "ping", "options": []},
        "timeout": 0.2,
    })
    # invoke returned (timed out, no bot listened). Now drive a callback
    # against the same token and check that subsequent state lookups work.
    payload = r.json()["payload"]
    cb = c.post(f"/api/v10/interactions/{payload['id']}/{payload['token']}/callback",
                json={"type": 4, "data": {"content": "pong!"}})
    assert cb.status_code in (200, 204), cb.text
    # original_message_id should now be set
    assert WORLD.interaction_tokens[payload["token"]].get("original_message_id")


def test_save_list_load_delete_testcase() -> None:
    c, ctx = _setup_world()
    body = {
        "name": "ping-basic",
        "description": "/ping should respond",
        "kind": "application_command",
        "application_id": ctx["app_id"],
        "user_id": ctx["owner_id"],
        "channel_id": ctx["channel_id"],
        "guild_id": ctx["guild_id"],
        "invocation": {"command_name": "ping", "options": []},
        "expect": {"content_contains": "pong", "response_type": 4},
    }
    assert c.post("/workbench/testcases", json=body).status_code == 200
    listed = c.get("/workbench/testcases").json()
    assert any(t["name"] == "ping-basic" for t in listed)

    loaded = c.get("/workbench/testcases/ping-basic").json()
    assert loaded["invocation"]["command_name"] == "ping"

    assert c.delete("/workbench/testcases/ping-basic").status_code == 204
    listed_after = c.get("/workbench/testcases").json()
    assert not any(t["name"] == "ping-basic" for t in listed_after)


def test_run_testcase_with_no_bot_fails_cleanly() -> None:
    c, ctx = _setup_world()
    body = {
        "name": "expects-pong",
        "kind": "application_command",
        "application_id": ctx["app_id"],
        "user_id": ctx["owner_id"],
        "channel_id": ctx["channel_id"],
        "guild_id": ctx["guild_id"],
        "invocation": {"command_name": "ping", "options": []},
        "expect": {"content_contains": "pong", "response_type": 4},
    }
    c.post("/workbench/testcases", json=body)
    r = c.post("/workbench/testcases/expects-pong/run", json={"timeout": 0.2})
    assert r.status_code == 200
    rep = r.json()
    # Without a bot wired up we expect failure with a clear reason.
    assert rep["passed"] is False
    assert any("no response" in reason.lower() or "content" in reason.lower()
               for reason in rep["reasons"])


def test_run_all_summarises_passes_and_fails() -> None:
    c, ctx = _setup_world()
    for name in ["a", "b"]:
        c.post("/workbench/testcases", json={
            "name": name,
            "kind": "application_command",
            "application_id": ctx["app_id"],
            "user_id": ctx["owner_id"],
            "channel_id": ctx["channel_id"],
            "guild_id": ctx["guild_id"],
            "invocation": {"command_name": "ping", "options": []},
            "expect": {"content_contains": "pong"},
        })
    r = c.post("/workbench/run-all", json={"timeout": 0.1}).json()
    assert r["total"] == 2
    # Without a bot, both fail; the point is the structure.
    assert r["passed"] + r["failed"] == 2


def test_runner_can_evaluate_a_response_after_callback() -> None:
    """End-to-end inside a single process: dispatch, callback, evaluate.

    Mirrors what `python -m dapi_emu.workbench run` would do once a bot is
    available.
    """
    c, ctx = _setup_world()
    payload = runner.build_application_command(
        application_id=ctx["app_id"],
        user_id=ctx["owner_id"],
        channel_id=ctx["channel_id"],
        guild_id=ctx["guild_id"],
        command_name="ping",
        options=[],
    )
    pre = runner.snapshot_channel_messages(ctx["channel_id"])
    runner.dispatch(payload)

    # "bot" replies via the callback endpoint
    r = c.post(f"/api/v10/interactions/{payload['id']}/{payload['token']}/callback",
               json={"type": 4, "data": {"content": "pong! arg=hi"}})
    assert r.status_code in (200, 204), r.text

    result = runner.collect_response(payload=payload, pre_message_ids=pre,
                                     timeout=0.2)
    assert result.original_message is not None
    assert "pong" in result.original_message["content"]

    # `response_type` in expectations matches Message.type — a callback type=4
    # produces a message with type=0 (DEFAULT) in our emulator, so we only
    # assert on the content here.
    verdict = runner.evaluate_expectation(result, {"content_contains": "pong"})
    assert verdict["passed"], verdict["reasons"]

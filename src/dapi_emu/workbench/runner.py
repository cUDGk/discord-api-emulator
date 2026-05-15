"""Build interaction payloads, dispatch them, capture the bot's response.

Shared by the WebUI routes and the CLI runner. Pure-Python; no FastAPI deps.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..snowflake import generate as new_snowflake
from ..state import WORLD


# Discord interaction types
TYPE_PING = 1
TYPE_APPLICATION_COMMAND = 2
TYPE_MESSAGE_COMPONENT = 3
TYPE_AUTOCOMPLETE = 4
TYPE_MODAL_SUBMIT = 5


def testcase_dir() -> Path:
    """Where testcases are stored. Override with DAPI_TESTCASES_DIR."""
    d = Path(os.environ.get(
        "DAPI_TESTCASES_DIR",
        Path(__file__).parent.parent.parent.parent / "data" / "testcases",
    ))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_token() -> str:
    return "wb_" + secrets.token_urlsafe(20)


def _resolve_user(user_id: str) -> dict:
    u = WORLD.users.get(user_id)
    if u is None:
        return {"id": user_id, "username": "unknown", "discriminator": "0",
                "global_name": None, "avatar": None, "bot": False,
                "system": False, "flags": 0, "public_flags": 0}
    return u.to_dict()


def _resolve_member(user_id: str, guild_id: Optional[str]) -> Optional[dict]:
    if not guild_id:
        return None
    m = WORLD.members.get((user_id, guild_id))
    if not m:
        return None
    d = m.to_dict(WORLD.users)
    d.pop("user", None)  # member object in interaction payload doesn't repeat user
    return d


def build_application_command(
    *,
    application_id: str,
    user_id: str,
    channel_id: str,
    guild_id: Optional[str] = None,
    command_name: str,
    command_type: int = 1,
    options: Optional[list[dict]] = None,
) -> dict:
    """Build an APPLICATION_COMMAND interaction payload (type=2)."""
    return {
        "id": new_snowflake(),
        "application_id": application_id,
        "type": TYPE_APPLICATION_COMMAND,
        "data": {
            "id": new_snowflake(),
            "name": command_name,
            "type": command_type,
            "options": options or [],
        },
        "guild_id": guild_id,
        "channel_id": channel_id,
        "user": _resolve_user(user_id),
        "member": _resolve_member(user_id, guild_id),
        "token": _new_token(),
        "version": 1,
        "app_permissions": "0",
        "locale": "en-US",
        "guild_locale": "en-US" if guild_id else None,
    }


def build_message_component(
    *,
    application_id: str,
    user_id: str,
    channel_id: str,
    guild_id: Optional[str] = None,
    custom_id: str,
    component_type: int = 2,  # 2=Button, 3=Select, 5..8=other selects
    values: Optional[list[str]] = None,
    message_id: Optional[str] = None,
) -> dict:
    """Build a MESSAGE_COMPONENT interaction payload (type=3, button/select)."""
    data: dict[str, Any] = {
        "custom_id": custom_id,
        "component_type": component_type,
    }
    if values is not None:
        data["values"] = values
    return {
        "id": new_snowflake(),
        "application_id": application_id,
        "type": TYPE_MESSAGE_COMPONENT,
        "data": data,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "user": _resolve_user(user_id),
        "member": _resolve_member(user_id, guild_id),
        "token": _new_token(),
        "version": 1,
        "message": ({"id": message_id, "channel_id": channel_id}
                    if message_id else None),
        "app_permissions": "0",
        "locale": "en-US",
        "guild_locale": "en-US" if guild_id else None,
    }


def build_modal_submit(
    *,
    application_id: str,
    user_id: str,
    channel_id: str,
    guild_id: Optional[str] = None,
    custom_id: str,
    components: Optional[list[dict]] = None,
) -> dict:
    """Build a MODAL_SUBMIT interaction payload (type=5)."""
    return {
        "id": new_snowflake(),
        "application_id": application_id,
        "type": TYPE_MODAL_SUBMIT,
        "data": {
            "custom_id": custom_id,
            "components": components or [],
        },
        "guild_id": guild_id,
        "channel_id": channel_id,
        "user": _resolve_user(user_id),
        "member": _resolve_member(user_id, guild_id),
        "token": _new_token(),
        "version": 1,
        "app_permissions": "0",
        "locale": "en-US",
        "guild_locale": "en-US" if guild_id else None,
    }


def dispatch(payload: dict) -> None:
    """Register the token so callback routes accept it, then publish
    INTERACTION_CREATE on the in-process EventBus (bot's WS session picks it up).
    """
    token = payload["token"]
    WORLD.interaction_tokens[token] = {
        "interaction_id": payload["id"],
        "interaction_type": payload["type"],
        "application_id": payload["application_id"],
        "channel_id": payload["channel_id"],
        "guild_id": payload.get("guild_id"),
        "user_id": payload["user"]["id"],
    }
    WORLD.bus.publish("INTERACTION_CREATE", payload)


@dataclass
class CaptureResult:
    """Snapshot of what the bot did in response to an interaction."""
    token: str
    interaction_id: str
    channel_id: str
    original_message: Optional[dict] = None
    followup_messages: list[dict] = field(default_factory=list)
    all_new_messages: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0


def collect_response(
    *,
    payload: dict,
    pre_message_ids: set[str],
    timeout: float = 2.5,
    poll_interval: float = 0.05,
) -> CaptureResult:
    """Poll for the bot's reaction until either an @original is set, the
    channel grows a new message, or we hit the timeout.

    pre_message_ids is the snapshot of message IDs that existed before dispatch —
    everything else captured here is new and likely caused by the interaction.
    """
    token = payload["token"]
    channel_id = payload["channel_id"]
    started = time.time()
    deadline = started + timeout

    # Poll on either of two signals: a callback set original_message_id, OR
    # any new message landed in the channel (covers webhook execute / raw
    # MESSAGE_CREATE / followups that arrive before the callback does).
    while time.time() < deadline:
        ctx = WORLD.interaction_tokens.get(token, {})
        if ctx.get("original_message_id"):
            break
        current = WORLD.channel_messages.get(channel_id, [])
        if any(mid not in pre_message_ids for mid in current):
            break
        time.sleep(poll_interval)

    ctx = WORLD.interaction_tokens.get(token, {})
    original_id = ctx.get("original_message_id")
    original = WORLD.messages.get(original_id) if original_id else None

    new_msgs: list[dict] = []
    for mid in WORLD.channel_messages.get(channel_id, []):
        if mid in pre_message_ids:
            continue
        m = WORLD.messages.get(mid)
        if m is not None:
            try:
                new_msgs.append(m.to_dict(WORLD.users))
            except KeyError:
                # author user got cleaned up since dispatch; skip rendering
                continue

    followups = [m for m in new_msgs if m["id"] != original_id]
    original_dict: Optional[dict] = None
    if original is not None:
        try:
            original_dict = original.to_dict(WORLD.users)
        except KeyError:
            original_dict = next((m for m in new_msgs
                                  if m["id"] == original_id), None)

    return CaptureResult(
        token=token,
        interaction_id=payload["id"],
        channel_id=channel_id,
        original_message=original_dict,
        followup_messages=followups,
        all_new_messages=new_msgs,
        elapsed_ms=int((time.time() - started) * 1000),
    )


def snapshot_channel_messages(channel_id: str) -> set[str]:
    return set(WORLD.channel_messages.get(channel_id, []))


# --- Testcase persistence -------------------------------------------------

def save_testcase(name: str, body: dict) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    path = testcase_dir() / f"{safe}.json"
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def load_testcase(name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not p.is_file():
        p = testcase_dir() / f"{name_or_path}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def list_testcases() -> list[dict]:
    out: list[dict] = []
    for p in sorted(testcase_dir().glob("*.json")):
        try:
            body = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "name": p.stem,
            "kind": body.get("kind"),
            "description": body.get("description", ""),
            "command": (body.get("invocation") or {}).get("command_name"),
            "path": str(p),
        })
    return out


def delete_testcase(name: str) -> bool:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    path = testcase_dir() / f"{safe}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# --- Match expected assertions against a CaptureResult --------------------

def evaluate_expectation(result: CaptureResult, expect: dict) -> dict:
    """Return {"passed": bool, "reasons": [...]}; never raises."""
    reasons: list[str] = []
    msgs = result.all_new_messages
    primary = result.original_message or (msgs[0] if msgs else None)

    if expect.get("expect_response", True) and primary is None:
        reasons.append("no response message captured")

    min_msgs = expect.get("min_messages")
    if min_msgs is not None and len(msgs) < int(min_msgs):
        reasons.append(f"expected at least {min_msgs} messages, got {len(msgs)}")

    max_msgs = expect.get("max_messages")
    if max_msgs is not None and len(msgs) > int(max_msgs):
        reasons.append(f"expected at most {max_msgs} messages, got {len(msgs)}")

    if primary is not None:
        rt = expect.get("response_type")
        if rt is not None and primary.get("type") != int(rt):
            reasons.append(f"response type {primary.get('type')} != expected {rt}")
        needle = expect.get("content_contains")
        if needle and needle not in (primary.get("content") or ""):
            reasons.append(f"content does not contain {needle!r}")
        equals = expect.get("content_equals")
        if equals is not None and (primary.get("content") or "") != equals:
            reasons.append(f"content {primary.get('content')!r} != expected {equals!r}")
        ephemeral = expect.get("ephemeral")
        if ephemeral is not None:
            is_eph = bool(primary.get("flags", 0) & 64)
            if is_eph != bool(ephemeral):
                reasons.append(f"ephemeral={is_eph} != expected {ephemeral}")

    return {"passed": not reasons, "reasons": reasons}


def execute_testcase(body: dict, *, timeout: float = 2.5) -> dict:
    """Run a saved testcase in-process. Returns a report dict."""
    kind = body.get("kind")
    inv = body.get("invocation") or {}
    common = dict(
        application_id=body["application_id"],
        user_id=body["user_id"],
        channel_id=body["channel_id"],
        guild_id=body.get("guild_id"),
    )
    if kind == "application_command":
        payload = build_application_command(
            **common,
            command_name=inv["command_name"],
            command_type=int(inv.get("command_type", 1)),
            options=inv.get("options") or [],
        )
    elif kind == "component":
        payload = build_message_component(
            **common,
            custom_id=inv["custom_id"],
            component_type=int(inv.get("component_type", 2)),
            values=inv.get("values"),
            message_id=inv.get("message_id"),
        )
    elif kind == "modal":
        payload = build_modal_submit(
            **common,
            custom_id=inv["custom_id"],
            components=inv.get("components") or [],
        )
    else:
        return {"passed": False, "reasons": [f"unknown kind: {kind}"]}

    pre = snapshot_channel_messages(common["channel_id"])
    dispatch(payload)
    result = collect_response(payload=payload, pre_message_ids=pre, timeout=timeout)
    verdict = evaluate_expectation(result, body.get("expect") or {})
    return {
        "name": body.get("name"),
        "kind": kind,
        "passed": verdict["passed"],
        "reasons": verdict["reasons"],
        "elapsed_ms": result.elapsed_ms,
        "primary_response": result.original_message,
        "all_new_messages": result.all_new_messages,
    }

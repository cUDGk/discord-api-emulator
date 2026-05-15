"""Workbench REST + UI routes — /workbench, /workbench/...

Mounted at app root (NOT under /api/v10) since these are tooling endpoints,
not Discord-compatible API.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ..state import WORLD
from . import runner

router = APIRouter()


_TEMPLATE_PATH = Path(__file__).parent.parent / "panel" / "templates" / "workbench.html"


@router.get("/workbench", response_class=HTMLResponse)
async def workbench_index() -> HTMLResponse:
    if _TEMPLATE_PATH.exists():
        return HTMLResponse(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>workbench.html not found</h1>", status_code=500)


# --- Discoverable inventory ---------------------------------------------

@router.get("/workbench/commands")
async def list_commands(application_id: str | None = None) -> list[dict]:
    """Return every slash command registered for the given app (or all apps)."""
    out: list[dict] = []
    for cmd_id, cmd in WORLD.commands.items():
        if application_id and str(cmd.get("application_id")) != str(application_id):
            continue
        out.append({
            "id": cmd_id,
            "application_id": cmd.get("application_id"),
            "name": cmd.get("name"),
            "description": cmd.get("description"),
            "type": cmd.get("type", 1),
            "options": cmd.get("options", []),
            "guild_id": cmd.get("guild_id"),
        })
    return out


@router.get("/workbench/inventory")
async def workbench_inventory() -> dict:
    """One-shot snapshot of everything the UI needs to render forms."""
    return {
        "applications": [
            {"id": a.id, "name": a.name, "bot_id": a.bot_id}
            for a in WORLD.applications.values()
        ],
        "users": [
            {"id": u.id, "username": u.username, "bot": u.bot}
            for u in WORLD.users.values()
        ],
        "guilds": [
            {
                "id": g.id, "name": g.name,
                "channels": [
                    {"id": c, "name": WORLD.channels[c].name,
                     "type": WORLD.channels[c].type}
                    for c in g.channel_ids if c in WORLD.channels
                ],
            }
            for g in WORLD.guilds.values()
        ],
        "commands": [
            {"id": cid, "application_id": cmd.get("application_id"),
             "name": cmd.get("name"), "description": cmd.get("description"),
             "options": cmd.get("options", [])}
            for cid, cmd in WORLD.commands.items()
        ],
    }


# --- Run interactions ----------------------------------------------------

@router.post("/workbench/invoke")
async def invoke(body: dict) -> dict:
    """Build an interaction payload, dispatch it, capture the bot's response.

    body keys: kind ("application_command" | "component" | "modal"),
               application_id, user_id, channel_id, guild_id?,
               invocation (kind-specific),
               timeout? (seconds, default 2.5)
    """
    kind = body.get("kind")
    inv = body.get("invocation") or {}
    common = dict(
        application_id=body["application_id"],
        user_id=body["user_id"],
        channel_id=body["channel_id"],
        guild_id=body.get("guild_id"),
    )
    timeout = float(body.get("timeout", 2.5))

    if kind == "application_command":
        payload = runner.build_application_command(
            **common,
            command_name=inv["command_name"],
            command_type=int(inv.get("command_type", 1)),
            options=inv.get("options") or [],
        )
    elif kind == "component":
        payload = runner.build_message_component(
            **common,
            custom_id=inv["custom_id"],
            component_type=int(inv.get("component_type", 2)),
            values=inv.get("values"),
            message_id=inv.get("message_id"),
        )
    elif kind == "modal":
        payload = runner.build_modal_submit(
            **common,
            custom_id=inv["custom_id"],
            components=inv.get("components") or [],
        )
    else:
        raise HTTPException(400, f"unknown kind: {kind}")

    pre = runner.snapshot_channel_messages(common["channel_id"])
    runner.dispatch(payload)
    result = runner.collect_response(payload=payload, pre_message_ids=pre,
                                     timeout=timeout)
    return {
        "payload": payload,
        "original_message": result.original_message,
        "followup_messages": result.followup_messages,
        "all_new_messages": result.all_new_messages,
        "elapsed_ms": result.elapsed_ms,
    }


# --- Testcase persistence -----------------------------------------------

@router.get("/workbench/testcases")
async def workbench_list_testcases() -> list[dict]:
    return runner.list_testcases()


@router.post("/workbench/testcases")
async def workbench_save_testcase(body: dict) -> dict:
    name = body.get("name")
    if not name:
        raise HTTPException(400, "name required")
    path = runner.save_testcase(name, body)
    return {"ok": True, "path": str(path)}


@router.get("/workbench/testcases/{name}")
async def workbench_get_testcase(name: str) -> dict:
    try:
        return runner.load_testcase(name)
    except FileNotFoundError:
        raise HTTPException(404, "testcase not found")


@router.delete("/workbench/testcases/{name}", status_code=204)
async def workbench_delete_testcase(name: str) -> None:
    runner.delete_testcase(name)


@router.post("/workbench/testcases/{name}/run")
async def workbench_run_testcase(name: str, body: dict | None = None) -> dict:
    try:
        tc = runner.load_testcase(name)
    except FileNotFoundError:
        raise HTTPException(404, "testcase not found")
    timeout = float((body or {}).get("timeout", 2.5))
    return runner.execute_testcase(tc, timeout=timeout)


@router.post("/workbench/run-all")
async def workbench_run_all(body: dict | None = None) -> dict:
    """Run every saved testcase, return a summary."""
    timeout = float((body or {}).get("timeout", 2.5))
    cases = runner.list_testcases()
    reports: list[dict] = []
    passed = failed = 0
    for c in cases:
        tc = runner.load_testcase(c["name"])
        report = runner.execute_testcase(tc, timeout=timeout)
        report["name"] = c["name"]
        reports.append(report)
        if report["passed"]:
            passed += 1
        else:
            failed += 1
    return {"total": len(cases), "passed": passed, "failed": failed,
            "reports": reports}

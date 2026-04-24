"""Application Commands + Interaction Responses + Followup webhooks.

This router implements the subset of Discord's Interactions REST surface
that is exposed to bots: slash/user/message command CRUD, interaction
callbacks (type 1-12), and the followup/original webhook endpoints
addressed via interaction tokens.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


# --- helpers --------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_application(application_id: str):
    app = WORLD.applications.get(application_id)
    if not app:
        raise HTTPException(
            status_code=404,
            detail={"code": 10002, "message": "Unknown Application"},
        )
    return app


def _assert_bot_owns_app(bot: User, application_id: str) -> None:
    app = _require_application(application_id)
    if app.bot_id != bot.id:
        raise HTTPException(
            status_code=403,
            detail={"code": 50001, "message": "Missing Access"},
        )


def _validate_command_name(name: Any) -> str:
    if not isinstance(name, str) or not (1 <= len(name) <= 32):
        raise HTTPException(
            status_code=400,
            detail={
                "code": 50035,
                "message": "Invalid Form Body",
                "errors": {"name": {"_errors": [
                    {"code": "BASE_TYPE_BAD_LENGTH", "message": "Must be between 1 and 32 in length."}
                ]}},
            },
        )
    return name


def _build_command(
    application_id: str,
    body: dict[str, Any],
    *,
    guild_id: str | None,
    command_id: str | None = None,
) -> dict[str, Any]:
    name = _validate_command_name(body.get("name"))
    cmd_id = command_id or new_snowflake()
    cmd: dict[str, Any] = {
        "id": cmd_id,
        "application_id": application_id,
        "type": int(body.get("type") or 1),
        "name": name,
        "name_localizations": body.get("name_localizations"),
        "description": body.get("description") or ("" if int(body.get("type") or 1) != 1 else ""),
        "description_localizations": body.get("description_localizations"),
        "options": body.get("options") or [],
        "default_member_permissions": body.get("default_member_permissions"),
        "dm_permission": body.get("dm_permission", True),
        "nsfw": bool(body.get("nsfw") or False),
        "version": new_snowflake(),
    }
    if guild_id is not None:
        cmd["guild_id"] = guild_id
    return cmd


def _list_scope_ids(application_id: str, guild_id: str | None) -> list[str]:
    if guild_id is None:
        return WORLD.global_commands.setdefault(application_id, [])
    return WORLD.guild_commands.setdefault((application_id, guild_id), [])


def _find_by_name(application_id: str, guild_id: str | None, name: str) -> str | None:
    for cid in _list_scope_ids(application_id, guild_id):
        cmd = WORLD.commands.get(cid)
        if cmd and cmd.get("name") == name:
            return cid
    return None


def _get_command_or_404(
    application_id: str, command_id: str, guild_id: str | None
) -> dict[str, Any]:
    ids = _list_scope_ids(application_id, guild_id)
    if command_id not in ids or command_id not in WORLD.commands:
        raise HTTPException(
            status_code=404,
            detail={"code": 10063, "message": "Unknown application command"},
        )
    return WORLD.commands[command_id]


def _delete_command(application_id: str, command_id: str, guild_id: str | None) -> None:
    ids = _list_scope_ids(application_id, guild_id)
    if command_id in ids:
        ids.remove(command_id)
    WORLD.commands.pop(command_id, None)


# --- Global commands ------------------------------------------------------

@router.get("/applications/{application_id}/commands")
async def list_global_commands(
    application_id: str,
    with_localizations: bool = Query(False),
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_application(application_id)
    return [
        WORLD.commands[cid]
        for cid in WORLD.global_commands.get(application_id, [])
        if cid in WORLD.commands
    ]


@router.post("/applications/{application_id}/commands")
async def create_global_command(
    application_id: str,
    body: dict,
    bot: User = Depends(require_bot),
) -> dict:
    _assert_bot_owns_app(bot, application_id)
    name = _validate_command_name(body.get("name"))
    # upsert by name
    existing_id = _find_by_name(application_id, None, name)
    if existing_id:
        cmd = _build_command(application_id, body, guild_id=None, command_id=existing_id)
        WORLD.commands[existing_id] = cmd
        return cmd
    cmd = _build_command(application_id, body, guild_id=None)
    WORLD.commands[cmd["id"]] = cmd
    WORLD.global_commands.setdefault(application_id, []).append(cmd["id"])
    return cmd


@router.get("/applications/{application_id}/commands/{command_id}")
async def get_global_command(
    application_id: str,
    command_id: str,
    _bot: User = Depends(require_bot),
) -> dict:
    _require_application(application_id)
    return _get_command_or_404(application_id, command_id, None)


@router.patch("/applications/{application_id}/commands/{command_id}")
async def edit_global_command(
    application_id: str,
    command_id: str,
    body: dict,
    bot: User = Depends(require_bot),
) -> dict:
    _assert_bot_owns_app(bot, application_id)
    current = _get_command_or_404(application_id, command_id, None)
    merged = dict(current)
    for field in (
        "name", "description", "options", "default_member_permissions",
        "dm_permission", "nsfw", "type", "name_localizations", "description_localizations",
    ):
        if field in body:
            merged[field] = body[field]
    if "name" in body:
        _validate_command_name(merged["name"])
    merged["version"] = new_snowflake()
    WORLD.commands[command_id] = merged
    return merged


@router.delete("/applications/{application_id}/commands/{command_id}", status_code=204)
async def delete_global_command(
    application_id: str,
    command_id: str,
    bot: User = Depends(require_bot),
):
    _assert_bot_owns_app(bot, application_id)
    _get_command_or_404(application_id, command_id, None)
    _delete_command(application_id, command_id, None)
    return Response(status_code=204)


@router.put("/applications/{application_id}/commands")
async def bulk_overwrite_global_commands(
    application_id: str,
    body: list[dict],
    bot: User = Depends(require_bot),
) -> list[dict]:
    _assert_bot_owns_app(bot, application_id)
    # drop every existing global command for this app
    for old_id in list(WORLD.global_commands.get(application_id, [])):
        WORLD.commands.pop(old_id, None)
    WORLD.global_commands[application_id] = []

    out: list[dict] = []
    for entry in body or []:
        cmd = _build_command(application_id, entry, guild_id=None)
        WORLD.commands[cmd["id"]] = cmd
        WORLD.global_commands[application_id].append(cmd["id"])
        out.append(cmd)
    return out


# --- Guild commands -------------------------------------------------------

@router.get("/applications/{application_id}/guilds/{guild_id}/commands")
async def list_guild_commands(
    application_id: str,
    guild_id: str,
    with_localizations: bool = Query(False),
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_application(application_id)
    return [
        WORLD.commands[cid]
        for cid in WORLD.guild_commands.get((application_id, guild_id), [])
        if cid in WORLD.commands
    ]


@router.post("/applications/{application_id}/guilds/{guild_id}/commands")
async def create_guild_command(
    application_id: str,
    guild_id: str,
    body: dict,
    bot: User = Depends(require_bot),
) -> dict:
    _assert_bot_owns_app(bot, application_id)
    name = _validate_command_name(body.get("name"))
    existing_id = _find_by_name(application_id, guild_id, name)
    if existing_id:
        cmd = _build_command(application_id, body, guild_id=guild_id, command_id=existing_id)
        WORLD.commands[existing_id] = cmd
        return cmd
    cmd = _build_command(application_id, body, guild_id=guild_id)
    WORLD.commands[cmd["id"]] = cmd
    WORLD.guild_commands.setdefault((application_id, guild_id), []).append(cmd["id"])
    return cmd


@router.get("/applications/{application_id}/guilds/{guild_id}/commands/{command_id}")
async def get_guild_command(
    application_id: str,
    guild_id: str,
    command_id: str,
    _bot: User = Depends(require_bot),
) -> dict:
    _require_application(application_id)
    return _get_command_or_404(application_id, command_id, guild_id)


@router.patch("/applications/{application_id}/guilds/{guild_id}/commands/{command_id}")
async def edit_guild_command(
    application_id: str,
    guild_id: str,
    command_id: str,
    body: dict,
    bot: User = Depends(require_bot),
) -> dict:
    _assert_bot_owns_app(bot, application_id)
    current = _get_command_or_404(application_id, command_id, guild_id)
    merged = dict(current)
    for field in (
        "name", "description", "options", "default_member_permissions",
        "dm_permission", "nsfw", "type", "name_localizations", "description_localizations",
    ):
        if field in body:
            merged[field] = body[field]
    if "name" in body:
        _validate_command_name(merged["name"])
    merged["version"] = new_snowflake()
    WORLD.commands[command_id] = merged
    return merged


@router.delete(
    "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}",
    status_code=204,
)
async def delete_guild_command(
    application_id: str,
    guild_id: str,
    command_id: str,
    bot: User = Depends(require_bot),
):
    _assert_bot_owns_app(bot, application_id)
    _get_command_or_404(application_id, command_id, guild_id)
    _delete_command(application_id, command_id, guild_id)
    return Response(status_code=204)


@router.put("/applications/{application_id}/guilds/{guild_id}/commands")
async def bulk_overwrite_guild_commands(
    application_id: str,
    guild_id: str,
    body: list[dict],
    bot: User = Depends(require_bot),
) -> list[dict]:
    _assert_bot_owns_app(bot, application_id)
    key = (application_id, guild_id)
    for old_id in list(WORLD.guild_commands.get(key, [])):
        WORLD.commands.pop(old_id, None)
    WORLD.guild_commands[key] = []

    out: list[dict] = []
    for entry in body or []:
        cmd = _build_command(application_id, entry, guild_id=guild_id)
        WORLD.commands[cmd["id"]] = cmd
        WORLD.guild_commands[key].append(cmd["id"])
        out.append(cmd)
    return out


# --- Command permissions --------------------------------------------------

@router.get("/applications/{application_id}/guilds/{guild_id}/commands/permissions")
async def list_guild_command_permissions(
    application_id: str,
    guild_id: str,
    _bot: User = Depends(require_bot),
) -> list[dict]:
    _require_application(application_id)
    return []


@router.get(
    "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}/permissions"
)
async def get_guild_command_permissions(
    application_id: str,
    guild_id: str,
    command_id: str,
    _bot: User = Depends(require_bot),
) -> dict:
    _require_application(application_id)
    _get_command_or_404(application_id, command_id, guild_id)
    return {
        "id": command_id,
        "application_id": application_id,
        "guild_id": guild_id,
        "permissions": [],
    }


@router.put(
    "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}/permissions"
)
async def set_guild_command_permissions(
    application_id: str,
    guild_id: str,
    command_id: str,
    body: dict,
    _bot: User = Depends(require_bot),
) -> dict:
    _require_application(application_id)
    _get_command_or_404(application_id, command_id, guild_id)
    return {
        "id": command_id,
        "application_id": application_id,
        "guild_id": guild_id,
        "permissions": body.get("permissions") or [],
    }


# --- Interaction callback -------------------------------------------------

class InteractionCallbackBody(BaseModel):
    type: int
    data: dict[str, Any] | None = None


def _require_interaction_token(token: str) -> dict[str, Any]:
    ctx = WORLD.interaction_tokens.get(token)
    if not ctx:
        raise HTTPException(
            status_code=404,
            detail={"code": 10015, "message": "Unknown Webhook"},
        )
    return ctx


def _wrap_callback_response(
    ctx: dict[str, Any], msg_payload: dict[str, Any] | None
) -> dict[str, Any]:
    """Shape of ApplicationCommandResponse when with_response=true."""
    iface: dict[str, Any] = {
        "interaction": {
            "id": ctx.get("interaction_id"),
            "type": ctx.get("interaction_type", 2),
            "application_id": ctx.get("application_id"),
            "response_message_id": msg_payload["id"] if msg_payload else None,
            "response_message_loading": False,
            "response_message_ephemeral": bool(
                msg_payload and (msg_payload.get("flags", 0) & 64)
            ),
        },
        "resource": None,
    }
    if msg_payload is not None:
        iface["resource"] = {"type": 4, "message": msg_payload}
    return iface


@router.post("/interactions/{interaction_id}/{interaction_token}/callback")
async def create_interaction_response(
    interaction_id: str,
    interaction_token: str,
    body: InteractionCallbackBody,
    with_response: bool = Query(False),
):
    ctx = _require_interaction_token(interaction_token)
    if ctx.get("interaction_id") and ctx["interaction_id"] != interaction_id:
        raise HTTPException(
            status_code=404,
            detail={"code": 10015, "message": "Unknown Webhook"},
        )

    data = body.data or {}
    channel_id = ctx.get("channel_id")
    app_id = ctx.get("application_id")
    msg_payload: dict[str, Any] | None = None

    callback_type = body.type

    if callback_type == 1:
        # PONG — nothing to publish
        pass

    elif callback_type in (4, 5):
        # CHANNEL_MESSAGE_WITH_SOURCE / DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        if channel_id:
            content = "" if callback_type == 5 else (data.get("content") or "")
            flags = int(data.get("flags") or 0)
            if callback_type == 5:
                flags |= 128  # LOADING
            msg = WORLD.create_message(
                channel_id=channel_id,
                author_id=app_id or ctx.get("user_id", ""),
                content=content,
                embeds=data.get("embeds") or [],
                components=data.get("components") or [],
                flags=flags,
                tts=bool(data.get("tts")),
            )
            ctx["original_message_id"] = msg.id
            msg_payload = msg.to_dict(WORLD.users)
            WORLD.bus.publish("MESSAGE_CREATE", msg_payload)

    elif callback_type == 6:
        # DEFERRED_UPDATE_MESSAGE — component: no visible change yet
        pass

    elif callback_type == 7:
        # UPDATE_MESSAGE — edit the component's source message
        original_id = ctx.get("original_message_id")
        if original_id and original_id in WORLD.messages:
            m = WORLD.messages[original_id]
            if "content" in data:
                m.content = data.get("content") or ""
            if "embeds" in data:
                m.embeds = data.get("embeds") or []
            if "components" in data:
                m.components = data.get("components") or []
            if "flags" in data:
                m.flags = int(data.get("flags") or 0)
            m.edited_timestamp = _now_iso()
            msg_payload = m.to_dict(WORLD.users)
            WORLD.bus.publish("MESSAGE_UPDATE", msg_payload)

    elif callback_type == 8:
        # APPLICATION_COMMAND_AUTOCOMPLETE_RESULT — ACK only
        pass

    elif callback_type == 9:
        # MODAL — client-side only, ACK
        pass

    elif callback_type == 12:
        # PREMIUM_REQUIRED / launch activity variant — ACK
        pass

    else:
        raise HTTPException(
            status_code=400,
            detail={"code": 50035, "message": "Invalid interaction response type"},
        )

    if with_response:
        return _wrap_callback_response(ctx, msg_payload)
    return Response(status_code=204)


# --- Followup webhooks (interaction token) --------------------------------

def _token_scoped_message(token: str, message_id: str):
    ctx = _require_interaction_token(token)
    m = WORLD.messages.get(message_id)
    if not m:
        raise HTTPException(
            status_code=404,
            detail={"code": 10008, "message": "Unknown Message"},
        )
    if ctx.get("channel_id") and m.channel_id != ctx["channel_id"]:
        raise HTTPException(
            status_code=404,
            detail={"code": 10008, "message": "Unknown Message"},
        )
    return ctx, m


def _resolve_original_id(ctx: dict[str, Any]) -> str:
    mid = ctx.get("original_message_id")
    if not mid or mid not in WORLD.messages:
        raise HTTPException(
            status_code=404,
            detail={"code": 10008, "message": "Unknown Message"},
        )
    return mid


@router.get("/webhooks/{application_id}/{interaction_token}/messages/@original")
async def get_original_interaction_response(
    application_id: str,
    interaction_token: str,
) -> dict:
    ctx = _require_interaction_token(interaction_token)
    mid = _resolve_original_id(ctx)
    return WORLD.messages[mid].to_dict(WORLD.users)


@router.patch("/webhooks/{application_id}/{interaction_token}/messages/@original")
async def edit_original_interaction_response(
    application_id: str,
    interaction_token: str,
    body: dict,
) -> dict:
    ctx = _require_interaction_token(interaction_token)
    mid = _resolve_original_id(ctx)
    m = WORLD.messages[mid]
    if "content" in body:
        m.content = body.get("content") or ""
    if "embeds" in body:
        m.embeds = body.get("embeds") or []
    if "components" in body:
        m.components = body.get("components") or []
    if "flags" in body:
        # clear LOADING bit once we actually edit
        m.flags = int(body.get("flags") or 0)
    else:
        if m.flags & 128:
            m.flags &= ~128
    if "attachments" in body:
        m.attachments = body.get("attachments") or []
    m.edited_timestamp = _now_iso()
    payload = m.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_UPDATE", payload)
    return payload


@router.delete(
    "/webhooks/{application_id}/{interaction_token}/messages/@original",
    status_code=204,
)
async def delete_original_interaction_response(
    application_id: str,
    interaction_token: str,
):
    ctx = _require_interaction_token(interaction_token)
    mid = _resolve_original_id(ctx)
    m = WORLD.messages.pop(mid, None)
    if m and mid in WORLD.channel_messages.get(m.channel_id, []):
        WORLD.channel_messages[m.channel_id].remove(mid)
    ctx.pop("original_message_id", None)
    if m:
        WORLD.bus.publish("MESSAGE_DELETE", {
            "id": mid, "channel_id": m.channel_id, "guild_id": m.guild_id,
        })
    return Response(status_code=204)


@router.post("/webhooks/{application_id}/{interaction_token}")
async def create_followup_message(
    application_id: str,
    interaction_token: str,
    body: dict,
    wait: bool = Query(True),
) -> dict:
    ctx = _require_interaction_token(interaction_token)
    channel_id = ctx.get("channel_id")
    if not channel_id:
        raise HTTPException(
            status_code=404,
            detail={"code": 10003, "message": "Unknown Channel"},
        )
    embeds = body.get("embeds") or ([body["embed"]] if body.get("embed") else [])
    msg = WORLD.create_message(
        channel_id=channel_id,
        author_id=application_id,
        content=body.get("content") or "",
        embeds=embeds,
        components=body.get("components") or [],
        flags=int(body.get("flags") or 0),
        tts=bool(body.get("tts")),
    )
    # First POST with no prior original? treat as the original.
    if not ctx.get("original_message_id"):
        ctx["original_message_id"] = msg.id
    payload = msg.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_CREATE", payload)
    return payload


@router.get("/webhooks/{application_id}/{interaction_token}/messages/{message_id}")
async def get_followup_message(
    application_id: str,
    interaction_token: str,
    message_id: str,
) -> dict:
    _, m = _token_scoped_message(interaction_token, message_id)
    return m.to_dict(WORLD.users)


@router.patch("/webhooks/{application_id}/{interaction_token}/messages/{message_id}")
async def edit_followup_message(
    application_id: str,
    interaction_token: str,
    message_id: str,
    body: dict,
) -> dict:
    _, m = _token_scoped_message(interaction_token, message_id)
    if "content" in body:
        m.content = body.get("content") or ""
    if "embeds" in body:
        m.embeds = body.get("embeds") or []
    if "components" in body:
        m.components = body.get("components") or []
    if "flags" in body:
        m.flags = int(body.get("flags") or 0)
    if "attachments" in body:
        m.attachments = body.get("attachments") or []
    m.edited_timestamp = _now_iso()
    payload = m.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_UPDATE", payload)
    return payload


@router.delete(
    "/webhooks/{application_id}/{interaction_token}/messages/{message_id}",
    status_code=204,
)
async def delete_followup_message(
    application_id: str,
    interaction_token: str,
    message_id: str,
):
    ctx, m = _token_scoped_message(interaction_token, message_id)
    WORLD.messages.pop(message_id, None)
    if message_id in WORLD.channel_messages.get(m.channel_id, []):
        WORLD.channel_messages[m.channel_id].remove(message_id)
    if ctx.get("original_message_id") == message_id:
        ctx.pop("original_message_id", None)
    WORLD.bus.publish("MESSAGE_DELETE", {
        "id": message_id, "channel_id": m.channel_id, "guild_id": m.guild_id,
    })
    return Response(status_code=204)

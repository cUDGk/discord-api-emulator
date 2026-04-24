"""Webhook endpoints: CRUD, execution, GitHub/Slack adapters."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import audit
from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


# --- helpers --------------------------------------------------------------

def _require_channel(channel_id: str) -> Any:
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return ch


def _require_webhook(webhook_id: str) -> dict:
    wh = WORLD.webhooks.get(webhook_id)
    if not wh:
        raise HTTPException(status_code=404, detail={"code": 10015, "message": "Unknown Webhook"})
    return wh


def _require_webhook_token(webhook_id: str, token: str) -> dict:
    wh = _require_webhook(webhook_id)
    if wh.get("token") != token:
        # Discord returns 401 for bad webhook token
        raise HTTPException(status_code=401, detail={"code": 50027, "message": "Invalid Webhook Token"})
    return wh


def _build_webhook(*, channel_id: str, name: str, avatar: str | None,
                   user: User, guild_id: str | None) -> dict:
    wid = new_snowflake()
    token = f"wh.{wid}.{new_snowflake()}"
    wh: dict[str, Any] = {
        "id": wid,
        "type": 1,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "user": user.to_dict(),
        "name": name,
        "avatar": avatar,
        "token": token,
        "application_id": None,
        "url": f"/webhooks/{wid}/{token}",
    }
    WORLD.webhooks[wid] = wh
    return wh


def _webhook_author(wh: dict, *, username_override: str | None = None,
                    avatar_override: str | None = None) -> dict:
    return {
        "id": wh["id"],
        "username": username_override or wh.get("name") or "webhook",
        "discriminator": "0000",
        "avatar": avatar_override if avatar_override is not None else wh.get("avatar"),
        "bot": True,
        "webhook_id": wh["id"],
        "global_name": None,
        "flags": 0,
        "public_flags": 0,
        "system": False,
    }


# --- Create / List --------------------------------------------------------

@router.post("/channels/{channel_id}/webhooks", status_code=200)
async def create_webhook(
    channel_id: str, body: dict, bot: User = Depends(require_bot)
) -> dict:
    ch = _require_channel(channel_id)
    wh = _build_webhook(
        channel_id=channel_id,
        name=body.get("name", "Captain Hook"),
        avatar=body.get("avatar"),
        user=bot,
        guild_id=ch.guild_id,
    )
    WORLD.bus.publish("WEBHOOKS_UPDATE", {"guild_id": ch.guild_id, "channel_id": channel_id})
    audit.log(  # WEBHOOK_CREATE
        ch.guild_id, bot.id, wh["id"], 50,
        changes=[
            {"key": "name", "new_value": wh["name"]},
            {"key": "channel_id", "new_value": channel_id},
            {"key": "type", "new_value": wh["type"]},
        ],
    )
    return wh


@router.get("/channels/{channel_id}/webhooks")
async def list_channel_webhooks(
    channel_id: str, _bot: User = Depends(require_bot)
) -> list[dict]:
    _require_channel(channel_id)
    return [w for w in WORLD.webhooks.values() if w.get("channel_id") == channel_id]


@router.get("/guilds/{guild_id}/webhooks")
async def list_guild_webhooks(
    guild_id: str, _bot: User = Depends(require_bot)
) -> list[dict]:
    if guild_id not in WORLD.guilds:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return [w for w in WORLD.webhooks.values() if w.get("guild_id") == guild_id]


@router.get("/webhooks/{webhook_id}")
async def get_webhook(webhook_id: str, _bot: User = Depends(require_bot)) -> dict:
    return _require_webhook(webhook_id)


@router.get("/webhooks/{webhook_id}/{webhook_token}")
async def get_webhook_with_token(webhook_id: str, webhook_token: str) -> dict:
    wh = _require_webhook_token(webhook_id, webhook_token)
    # Discord strips the user field on the token route
    return {k: v for k, v in wh.items() if k != "user"}


# --- Modify ---------------------------------------------------------------

def _apply_webhook_patch(wh: dict, body: dict) -> None:
    for f in ("name", "avatar", "channel_id"):
        if f in body:
            wh[f] = body[f]


@router.patch("/webhooks/{webhook_id}")
async def modify_webhook(
    webhook_id: str, body: dict, bot: User = Depends(require_bot)
) -> dict:
    wh = _require_webhook(webhook_id)
    changes: list[dict] = []
    for f in ("name", "avatar", "channel_id"):
        if f in body:
            old = wh.get(f)
            new = body[f]
            if old != new:
                changes.append({"key": f, "old_value": old, "new_value": new})
    _apply_webhook_patch(wh, body)
    WORLD.bus.publish("WEBHOOKS_UPDATE", {
        "guild_id": wh.get("guild_id"), "channel_id": wh.get("channel_id"),
    })
    audit.log(wh.get("guild_id"), bot.id, webhook_id, 51, changes=changes)  # WEBHOOK_UPDATE
    return wh


@router.patch("/webhooks/{webhook_id}/{webhook_token}")
async def modify_webhook_with_token(
    webhook_id: str, webhook_token: str, body: dict
) -> dict:
    wh = _require_webhook_token(webhook_id, webhook_token)
    # token route cannot change channel_id
    patch = {k: v for k, v in body.items() if k in ("name", "avatar")}
    _apply_webhook_patch(wh, patch)
    return {k: v for k, v in wh.items() if k != "user"}


# --- Delete ---------------------------------------------------------------

@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: str, bot: User = Depends(require_bot)) -> None:
    wh = _require_webhook(webhook_id)
    name = wh.get("name")
    WORLD.webhooks.pop(webhook_id, None)
    WORLD.bus.publish("WEBHOOKS_UPDATE", {
        "guild_id": wh.get("guild_id"), "channel_id": wh.get("channel_id"),
    })
    audit.log(  # WEBHOOK_DELETE
        wh.get("guild_id"), bot.id, webhook_id, 52,
        changes=[{"key": "name", "old_value": name}],
    )


@router.delete("/webhooks/{webhook_id}/{webhook_token}", status_code=204)
async def delete_webhook_with_token(webhook_id: str, webhook_token: str) -> None:
    wh = _require_webhook_token(webhook_id, webhook_token)
    WORLD.webhooks.pop(webhook_id, None)
    WORLD.bus.publish("WEBHOOKS_UPDATE", {
        "guild_id": wh.get("guild_id"), "channel_id": wh.get("channel_id"),
    })


# --- Execute --------------------------------------------------------------

def _execute_as_webhook(
    wh: dict,
    *,
    content: str,
    embeds: list[dict[str, Any]],
    components: list[dict[str, Any]],
    flags: int,
    tts: bool,
    username_override: str | None,
    avatar_override: str | None,
) -> Any:
    channel_id = wh["channel_id"]
    if channel_id not in WORLD.channels:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    # create_message stores the author as wh user_id — we override via post-processing.
    # Use any known author_id; we rewrite author in to_dict payload below.
    msg = WORLD.create_message(
        channel_id=channel_id,
        author_id=wh["id"],  # placeholder — author dict is replaced below
        content=content,
        embeds=embeds,
        components=components,
        flags=flags,
        tts=tts,
        type=0,
    )
    # Ensure the author renders as the webhook even if wh.id is not in WORLD.users.
    author = _webhook_author(wh, username_override=username_override,
                             avatar_override=avatar_override)
    # Build payload manually because WORLD.users[wh["id"]] may not exist.
    payload: dict[str, Any] = {
        "id": msg.id,
        "channel_id": msg.channel_id,
        "guild_id": msg.guild_id,
        "author": author,
        "content": msg.content,
        "timestamp": msg.timestamp,
        "edited_timestamp": msg.edited_timestamp,
        "tts": msg.tts,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": msg.attachments,
        "embeds": msg.embeds,
        "components": msg.components,
        "reactions": msg.reactions,
        "pinned": msg.pinned,
        "type": msg.type,
        "flags": msg.flags,
        "referenced_message": None,
        "webhook_id": wh["id"],
    }
    WORLD.bus.publish("MESSAGE_CREATE", payload)
    return msg, payload


def _interaction_followup(application_id: str, token: str, body: dict) -> dict | None:
    """If this token belongs to an interaction, create a followup message and
    return its payload. Otherwise return None."""
    ctx = WORLD.interaction_tokens.get(token)
    if not ctx or str(ctx.get("application_id")) != str(application_id):
        return None
    channel_id = ctx.get("channel_id")
    if not channel_id:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    embeds = body.get("embeds") or ([body["embed"]] if body.get("embed") else [])
    _app = WORLD.applications.get(application_id)
    author_id = _app.bot_id if _app else application_id
    msg = WORLD.create_message(
        channel_id=channel_id,
        author_id=author_id,
        content=body.get("content") or "",
        embeds=embeds,
        components=body.get("components") or [],
        flags=int(body.get("flags") or 0),
        tts=bool(body.get("tts")),
    )
    if not ctx.get("original_message_id"):
        ctx["original_message_id"] = msg.id
    payload = msg.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_CREATE", payload)
    return payload


def _interaction_msg(application_id: str, token: str, message_id: str | None):
    """Resolve (ctx, Message) from an interaction token. message_id may be
    '@original' to mean ctx['original_message_id']."""
    ctx = WORLD.interaction_tokens.get(token)
    if not ctx or str(ctx.get("application_id")) != str(application_id):
        return None, None
    mid = ctx.get("original_message_id") if message_id in (None, "@original") else message_id
    if not mid:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    m = WORLD.messages.get(mid)
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    return ctx, m


@router.post("/webhooks/{webhook_id}/{webhook_token}")
async def execute_webhook(
    webhook_id: str,
    webhook_token: str,
    body: dict,
    wait: bool = Query(False),
    thread_id: str | None = Query(None),
) -> Any:
    # Interaction followup fallback (shared path with webhook execute).
    followup = _interaction_followup(webhook_id, webhook_token, body)
    if followup is not None:
        return followup

    wh = _require_webhook_token(webhook_id, webhook_token)
    # thread_id reroutes the message into the given thread
    wh_view = dict(wh)
    if thread_id:
        if thread_id not in WORLD.threads:
            raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
        wh_view["channel_id"] = thread_id
    embeds = body.get("embeds") or ([body["embed"]] if body.get("embed") else [])
    _msg, payload = _execute_as_webhook(
        wh_view,
        content=body.get("content") or "",
        embeds=embeds,
        components=body.get("components") or [],
        flags=int(body.get("flags", 0) or 0),
        tts=bool(body.get("tts", False)),
        username_override=body.get("username"),
        avatar_override=body.get("avatar_url"),
    )
    if wait:
        return payload
    # 204 No Content when wait is false
    from starlette.responses import Response
    return Response(status_code=204)


# --- Webhook messages -----------------------------------------------------

# --- @original (interaction-only) ---------------------------------------

@router.get("/webhooks/{webhook_id}/{webhook_token}/messages/@original")
async def get_original_message(webhook_id: str, webhook_token: str) -> dict:
    _, m = _interaction_msg(webhook_id, webhook_token, "@original")
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10015, "message": "Unknown Webhook"})
    return m.to_dict(WORLD.users)


@router.patch("/webhooks/{webhook_id}/{webhook_token}/messages/@original")
async def edit_original_message(
    webhook_id: str, webhook_token: str, body: dict,
) -> dict:
    _, m = _interaction_msg(webhook_id, webhook_token, "@original")
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10015, "message": "Unknown Webhook"})
    if "content" in body:
        m.content = body.get("content") or ""
    if "embeds" in body:
        m.embeds = body.get("embeds") or []
    if "components" in body:
        m.components = body.get("components") or []
    if "flags" in body:
        m.flags = int(body.get("flags") or 0)
    elif m.flags & 128:
        m.flags &= ~128  # clear LOADING
    if "attachments" in body:
        m.attachments = body.get("attachments") or []
    m.edited_timestamp = datetime.now(timezone.utc).isoformat()
    payload = m.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_UPDATE", payload)
    return payload


@router.delete(
    "/webhooks/{webhook_id}/{webhook_token}/messages/@original", status_code=204
)
async def delete_original_message(webhook_id: str, webhook_token: str) -> None:
    ctx, m = _interaction_msg(webhook_id, webhook_token, "@original")
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10015, "message": "Unknown Webhook"})
    mid = m.id
    WORLD.messages.pop(mid, None)
    if mid in WORLD.channel_messages.get(m.channel_id, []):
        WORLD.channel_messages[m.channel_id].remove(mid)
    if ctx:
        ctx.pop("original_message_id", None)
    WORLD.bus.publish("MESSAGE_DELETE", {
        "id": mid, "channel_id": m.channel_id, "guild_id": m.guild_id,
    })


# --- Webhook/Interaction messages ----------------------------------------

@router.get("/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}")
async def get_webhook_message(
    webhook_id: str, webhook_token: str, message_id: str
) -> dict:
    # Interaction token route?
    _, im = _interaction_msg(webhook_id, webhook_token, message_id)
    if im:
        return im.to_dict(WORLD.users)
    _require_webhook_token(webhook_id, webhook_token)
    m = WORLD.messages.get(message_id)
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    d = m.to_dict(WORLD.users) if m.author_id in WORLD.users else {
        "id": m.id, "channel_id": m.channel_id, "content": m.content, "embeds": m.embeds,
        "components": m.components, "attachments": m.attachments, "timestamp": m.timestamp,
        "edited_timestamp": m.edited_timestamp, "pinned": m.pinned, "type": m.type,
        "flags": m.flags, "tts": m.tts, "mention_everyone": False, "mentions": [],
        "mention_roles": [], "reactions": m.reactions, "referenced_message": None,
        "guild_id": m.guild_id,
    }
    d["webhook_id"] = webhook_id
    return d


@router.patch("/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}")
async def edit_webhook_message(
    webhook_id: str, webhook_token: str, message_id: str, body: dict
) -> dict:
    _, im = _interaction_msg(webhook_id, webhook_token, message_id)
    if im:
        if "content" in body:
            im.content = body.get("content") or ""
        if "embeds" in body:
            im.embeds = body.get("embeds") or []
        if "components" in body:
            im.components = body.get("components") or []
        if "flags" in body:
            im.flags = int(body.get("flags") or 0)
        im.edited_timestamp = datetime.now(timezone.utc).isoformat()
        payload = im.to_dict(WORLD.users)
        WORLD.bus.publish("MESSAGE_UPDATE", payload)
        return payload

    _require_webhook_token(webhook_id, webhook_token)
    m = WORLD.messages.get(message_id)
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    if "content" in body and body["content"] is not None:
        m.content = body["content"]
    if "embeds" in body and body["embeds"] is not None:
        m.embeds = body["embeds"]
    if "components" in body and body["components"] is not None:
        m.components = body["components"]
    if "flags" in body and body["flags"] is not None:
        m.flags = int(body["flags"])
    m.edited_timestamp = datetime.now(timezone.utc).isoformat()
    payload = {
        "id": m.id, "channel_id": m.channel_id, "guild_id": m.guild_id,
        "content": m.content, "embeds": m.embeds, "components": m.components,
        "attachments": m.attachments, "timestamp": m.timestamp,
        "edited_timestamp": m.edited_timestamp, "pinned": m.pinned, "type": m.type,
        "flags": m.flags, "tts": m.tts, "mention_everyone": False, "mentions": [],
        "mention_roles": [], "reactions": m.reactions, "referenced_message": None,
        "webhook_id": webhook_id,
    }
    WORLD.bus.publish("MESSAGE_UPDATE", payload)
    return payload


@router.delete(
    "/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}", status_code=204
)
async def delete_webhook_message(
    webhook_id: str, webhook_token: str, message_id: str
) -> None:
    ctx, im = _interaction_msg(webhook_id, webhook_token, message_id)
    if im:
        WORLD.messages.pop(im.id, None)
        if im.id in WORLD.channel_messages.get(im.channel_id, []):
            WORLD.channel_messages[im.channel_id].remove(im.id)
        if ctx and ctx.get("original_message_id") == im.id:
            ctx.pop("original_message_id", None)
        WORLD.bus.publish("MESSAGE_DELETE", {
            "id": im.id, "channel_id": im.channel_id, "guild_id": im.guild_id,
        })
        return

    _require_webhook_token(webhook_id, webhook_token)
    m = WORLD.messages.get(message_id)
    if not m:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    channel_id = m.channel_id
    WORLD.messages.pop(message_id, None)
    if message_id in WORLD.channel_messages.get(channel_id, []):
        WORLD.channel_messages[channel_id].remove(message_id)
    WORLD.bus.publish("MESSAGE_DELETE", {
        "id": message_id, "channel_id": channel_id, "guild_id": m.guild_id,
    })


# --- Platform adapters ----------------------------------------------------

@router.post("/webhooks/{webhook_id}/{webhook_token}/github")
async def execute_github_webhook(
    webhook_id: str, webhook_token: str, body: dict,
    wait: bool = Query(False),
) -> Any:
    wh = _require_webhook_token(webhook_id, webhook_token)
    # We don't parse the GitHub payload — emit a placeholder message so listeners
    # can observe the event. The real Discord service formats an embed here.
    _msg, payload = _execute_as_webhook(
        wh,
        content="",
        embeds=[{"title": "GitHub event", "description": "adapter invoked"}],
        components=[],
        flags=0,
        tts=False,
        username_override=None,
        avatar_override=None,
    )
    if wait:
        return payload
    from starlette.responses import Response
    return Response(status_code=204)


@router.post("/webhooks/{webhook_id}/{webhook_token}/slack")
async def execute_slack_webhook(
    webhook_id: str, webhook_token: str, body: dict,
    wait: bool = Query(False),
) -> Any:
    wh = _require_webhook_token(webhook_id, webhook_token)
    text = ""
    if isinstance(body, dict):
        text = body.get("text") or ""
    _msg, payload = _execute_as_webhook(
        wh,
        content=text,
        embeds=[],
        components=[],
        flags=0,
        tts=False,
        username_override=None,
        avatar_override=None,
    )
    if wait:
        return payload
    from starlette.responses import Response
    return Response(status_code=204)

"""Discord-compatible Gateway WebSocket endpoint."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import config
from ..snowflake import generate as new_snowflake
from ..state import WORLD

log = logging.getLogger("dapi.gateway")

router = APIRouter()

# Opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_PRESENCE_UPDATE = 3
OP_VOICE_STATE_UPDATE = 4
OP_RESUME = 6
OP_RECONNECT = 7
OP_REQUEST_GUILD_MEMBERS = 8
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11


class Session:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.session_id = new_snowflake()
        self.seq = 0
        self.user_id: str | None = None
        self.intents: int = 0
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        self.heartbeat_ok = True
        self.closed = False

    async def send(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            self.closed = True

    async def dispatch(self, event_name: str, data: dict[str, Any]) -> None:
        self.seq += 1
        await self.send({"op": OP_DISPATCH, "t": event_name, "s": self.seq, "d": data})


# Privileged intent bits
INTENT_GUILD_MEMBERS = 1 << 1
INTENT_GUILD_PRESENCES = 1 << 8
INTENT_MESSAGE_CONTENT = 1 << 15


def _strip_message_content(msg: dict[str, Any]) -> dict[str, Any]:
    """When MESSAGE_CONTENT intent is missing, content/embeds/attachments/components
    must be blanked unless the bot is mentioned / DM'd / is the author."""
    m = dict(msg)
    m["content"] = ""
    m["embeds"] = []
    m["attachments"] = []
    m["components"] = []
    return m


# Events that don't belong to a specific guild/channel — always deliver.
_BROADCAST_EVENTS = {
    "READY", "RESUMED", "USER_UPDATE", "APPLICATION_COMMAND_PERMISSIONS_UPDATE",
}


def _resolve_guild_id(data: dict[str, Any]) -> str | None:
    gid = data.get("guild_id")
    if gid:
        return gid
    channel_id = data.get("channel_id") or data.get("id")
    if channel_id and channel_id in WORLD.channels:
        return WORLD.channels[channel_id].guild_id
    return None


def _should_deliver(session: "Session", event_name: str, data: dict[str, Any]) -> bool:
    """Return True iff this bot should receive this event.

    Isolates bots: each bot only sees events from guilds it is a member of,
    DMs it participates in, or interactions targeted at its application.
    """
    user_id = session.user_id
    if user_id is None:
        return False

    if event_name in _BROADCAST_EVENTS:
        return True

    # Interactions: only the owning application receives them
    if event_name == "INTERACTION_CREATE":
        app_id = data.get("application_id")
        if not app_id:
            return False
        app = WORLD.applications.get(app_id)
        return bool(app and app.bot_id == user_id)

    # Guild-scoped events
    guild_id = _resolve_guild_id(data)
    if guild_id is not None:
        return (user_id, guild_id) in WORLD.members

    # DM / group DM by channel recipients
    channel_id = data.get("channel_id") or data.get("id")
    if channel_id and channel_id in WORLD.channels:
        ch = WORLD.channels[channel_id]
        if ch.type in (1, 3):
            return user_id in ch.recipients

    # Fallback: if the payload carries a user_id, only deliver to self
    if "user_id" in data:
        return data["user_id"] == user_id

    # Otherwise don't leak
    return False


@router.websocket("/gateway")
async def gateway_ws(ws: WebSocket) -> None:
    await ws.accept()
    session = Session(ws)
    log.info("gateway: new connection %s", session.session_id)

    # Send Hello
    await session.send({
        "op": OP_HELLO,
        "d": {"heartbeat_interval": config.HEARTBEAT_INTERVAL_MS},
    })

    sender_task: asyncio.Task | None = None

    async def event_sender() -> None:
        assert session.queue is not None
        while not session.closed:
            try:
                event_name, data = await session.queue.get()
            except asyncio.CancelledError:
                return
            if not _should_deliver(session, event_name, data):
                continue
            if event_name == "MESSAGE_CREATE" and not (session.intents & INTENT_MESSAGE_CONTENT):
                author_id = data.get("author", {}).get("id")
                mentioned_ids = {u.get("id") for u in data.get("mentions", [])}
                if author_id != session.user_id and session.user_id not in mentioned_ids:
                    data = _strip_message_content(data)
            await session.dispatch(event_name, data)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.close(code=4002)
                return
            op = msg.get("op")

            if op == OP_HEARTBEAT:
                await session.send({"op": OP_HEARTBEAT_ACK})

            elif op == OP_IDENTIFY:
                d = msg.get("d") or {}
                token_raw: str = d.get("token", "")
                if token_raw.startswith("Bot "):
                    token_raw = token_raw[4:]
                user_id = WORLD.bot_tokens.get(token_raw)
                if not user_id:
                    await ws.close(code=4004)  # Authentication failed
                    return
                session.user_id = user_id
                session.intents = int(d.get("intents", 0))

                # Subscribe to event bus
                session.queue = await WORLD.bus.subscribe()
                sender_task = asyncio.create_task(event_sender())

                # Find application
                app = next((a for a in WORLD.applications.values() if a.bot_id == user_id), None)
                user = WORLD.users[user_id]

                # READY payload
                guilds_for_bot = [
                    {"id": g.id, "unavailable": True}
                    for g in WORLD.guilds.values()
                    if (user_id, g.id) in WORLD.members
                ]

                ready_d = {
                    "v": config.API_VERSION,
                    "user": user.to_dict(private=True),
                    "guilds": guilds_for_bot,
                    "session_id": session.session_id,
                    "resume_gateway_url": config.GATEWAY_WS_URL,
                    "application": {
                        "id": app.id if app else user_id,
                        "flags": app.flags if app else 0,
                    },
                    "shard": d.get("shard", [0, 1]),
                    "session_type": "normal",
                    "auth": {},
                    "geo_ordered_rtc_regions": ["us-east"],
                    "user_settings": {},
                    "relationships": [],
                    "private_channels": [],
                    "presences": [],
                    "guild_join_requests": [],
                }
                await session.dispatch("READY", ready_d)

                # Flush full GUILD_CREATE for each guild
                for g in list(WORLD.guilds.values()):
                    if (user_id, g.id) in WORLD.members:
                        await session.dispatch("GUILD_CREATE", g.to_dict(WORLD, full=True))

            elif op == OP_HEARTBEAT:
                await session.send({"op": OP_HEARTBEAT_ACK})

            elif op == OP_RESUME:
                # Naive: treat as new identify via Invalid Session
                await session.send({"op": OP_INVALID_SESSION, "d": False})
                await ws.close(code=4009)
                return

            elif op == OP_PRESENCE_UPDATE:
                d = msg.get("d") or {}
                if session.user_id:
                    WORLD.bus.publish("PRESENCE_UPDATE", {
                        "user": {"id": session.user_id},
                        "status": d.get("status", "online"),
                        "activities": d.get("activities", []),
                        "client_status": {"desktop": d.get("status", "online")},
                        "guild_id": None,
                    })

            elif op == OP_REQUEST_GUILD_MEMBERS:
                d = msg.get("d") or {}
                guild_id = d.get("guild_id")
                if isinstance(guild_id, list):
                    guild_ids = guild_id
                else:
                    guild_ids = [guild_id]
                for gid in guild_ids:
                    g = WORLD.guilds.get(gid)
                    if not g:
                        continue
                    chunk = [
                        WORLD.members[(uid, gid)].to_dict(WORLD.users)
                        for uid in g.member_ids
                        if (uid, gid) in WORLD.members
                    ]
                    await session.dispatch("GUILD_MEMBERS_CHUNK", {
                        "guild_id": gid,
                        "members": chunk,
                        "chunk_index": 0,
                        "chunk_count": 1,
                        "not_found": [],
                        "presences": [],
                        "nonce": d.get("nonce"),
                    })

            else:
                log.debug("gateway: unhandled op %s", op)

    except WebSocketDisconnect:
        log.info("gateway: disconnect %s", session.session_id)
    except Exception as e:
        log.exception("gateway: error: %s", e)
    finally:
        session.closed = True
        if sender_task:
            sender_task.cancel()
        if session.queue is not None:
            await WORLD.bus.unsubscribe(session.queue)

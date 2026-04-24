"""Discord-compatible Gateway WebSocket endpoint.

Supports:
- json / etf encoding (?encoding=json|etf)
- zlib-stream / zstd-stream compression (?compress=...)
- op 2 IDENTIFY / op 1 HEARTBEAT / op 6 RESUME (replays missed dispatches)
- per-bot event isolation via _should_deliver()
- sharding: IDENTIFY's `shard: [id, count]` filters guild-scoped dispatches
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Deque

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .. import config
from ..snowflake import generate as new_snowflake
from ..state import WORLD
from .encoding import Compressor, decode as wire_decode, encode as wire_encode

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

# How many past dispatches to retain per session for replay on RESUME.
RESUME_HISTORY_SIZE = 500
# How long a dropped session is resumable (seconds).
RESUME_TTL_SEC = 180


class Session:
    def __init__(
        self,
        ws: WebSocket,
        compress: str | None = None,
        encoding: str = "json",
    ) -> None:
        self.ws = ws
        self.session_id: str = new_snowflake()
        self.seq: int = 0
        self.user_id: str | None = None
        self.token: str | None = None
        self.intents: int = 0
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        self.closed = False

        self.encoding = encoding  # "json" or "etf"
        self.compress = compress  # None | "zlib-stream" | "zstd-stream"
        self._compressor = Compressor(compress)

        # Sharding: IDENTIFY may set these. Default = single shard.
        self.shard_id: int = 0
        self.shard_count: int = 1

        # Ring buffer of (seq, event_name, data) for RESUME replay.
        self.dispatch_history: Deque[tuple[int, str, dict[str, Any]]] = deque(
            maxlen=RESUME_HISTORY_SIZE,
        )

    async def send(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            wire = wire_encode(payload, self.encoding)
            if self._compressor.kind:
                raw = wire.encode("utf-8") if isinstance(wire, str) else wire
                await self.ws.send_bytes(self._compressor.compress(raw))
            elif isinstance(wire, str):
                await self.ws.send_text(wire)
            else:
                await self.ws.send_bytes(wire)
        except Exception:
            self.closed = True

    async def dispatch(self, event_name: str, data: dict[str, Any]) -> None:
        self.seq += 1
        self.dispatch_history.append((self.seq, event_name, data))
        await self.send(
            {"op": OP_DISPATCH, "t": event_name, "s": self.seq, "d": data}
        )

    def make_resumable(self) -> None:
        """Snapshot this session so a RESUME can pick it up. Called on disconnect."""
        if self.user_id is None:
            return
        WORLD.resumable_sessions[self.session_id] = {
            "user_id": self.user_id,
            "token": self.token,
            "intents": self.intents,
            "seq": self.seq,
            "history": list(self.dispatch_history),
            "expires_at": time.time() + RESUME_TTL_SEC,
        }


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


def _shard_matches(session: "Session", guild_id: str | None) -> bool:
    """Sharding filter: a guild belongs to shard `(guild_id >> 22) % count`.

    Sessions without an explicit shard tuple always have count == 1 so this
    short-circuits to True. We tolerate sessions that lack the attribute so
    `_should_deliver` stays unit-testable with a minimal fake session object.
    """
    count = getattr(session, "shard_count", 1) or 1
    if count == 1 or guild_id is None:
        return True
    try:
        return (int(guild_id) >> 22) % count == getattr(session, "shard_id", 0)
    except (TypeError, ValueError):
        return True


def _should_deliver(session: "Session", event_name: str, data: dict[str, Any]) -> bool:
    """Return True iff this bot should receive this event."""
    user_id = session.user_id
    if user_id is None:
        return False

    if event_name in _BROADCAST_EVENTS:
        return True

    if event_name == "INTERACTION_CREATE":
        app_id = data.get("application_id")
        if not app_id:
            return False
        app = WORLD.applications.get(app_id)
        if not (app and app.bot_id == user_id):
            return False
        return _shard_matches(session, data.get("guild_id"))

    guild_id = _resolve_guild_id(data)
    if guild_id is not None:
        if (user_id, guild_id) not in WORLD.members:
            return False
        return _shard_matches(session, guild_id)

    channel_id = data.get("channel_id") or data.get("id")
    if channel_id and channel_id in WORLD.channels:
        ch = WORLD.channels[channel_id]
        if ch.type in (1, 3):
            return user_id in ch.recipients

    if "user_id" in data:
        return data["user_id"] == user_id

    return False


def _gc_resumables() -> None:
    """Evict expired resumable sessions."""
    now = time.time()
    for sid in list(WORLD.resumable_sessions.keys()):
        if WORLD.resumable_sessions[sid]["expires_at"] < now:
            WORLD.resumable_sessions.pop(sid, None)


@router.websocket("/gateway")
async def gateway_ws(
    ws: WebSocket,
    v: str | None = Query(default=None),
    encoding: str | None = Query(default="json"),
    compress: str | None = Query(default=None),
) -> None:
    enc = (encoding or "json").lower()
    if enc not in ("json", "etf"):
        await ws.accept()
        await ws.close(code=4000)
        return

    comp = compress if compress in ("zlib-stream", "zstd-stream") else None

    await ws.accept()
    session = Session(ws, compress=comp, encoding=enc)
    log.info(
        "gateway: new connection session=%s encoding=%s compress=%s",
        session.session_id, session.encoding, session.compress,
    )

    # Hello
    await session.send(
        {"op": OP_HELLO, "d": {"heartbeat_interval": config.HEARTBEAT_INTERVAL_MS}}
    )

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

    async def start_event_pump() -> None:
        nonlocal sender_task
        session.queue = await WORLD.bus.subscribe()
        sender_task = asyncio.create_task(event_sender())

    try:
        while True:
            frame = await ws.receive()
            if frame.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(frame.get("code", 1000))
            raw: bytes | str
            if "text" in frame and frame["text"] is not None:
                raw = frame["text"]
            elif "bytes" in frame and frame["bytes"] is not None:
                raw = frame["bytes"]
            else:
                continue
            try:
                msg = wire_decode(raw, session.encoding)
            except (json.JSONDecodeError, ValueError):
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
                    await ws.close(code=4004)
                    return
                session.user_id = user_id
                session.token = token_raw
                session.intents = int(d.get("intents", 0))

                shard = d.get("shard")
                if isinstance(shard, (list, tuple)) and len(shard) == 2:
                    try:
                        session.shard_id = int(shard[0])
                        session.shard_count = max(1, int(shard[1]))
                    except (TypeError, ValueError):
                        pass

                await start_event_pump()

                app = next(
                    (a for a in WORLD.applications.values() if a.bot_id == user_id),
                    None,
                )
                user = WORLD.users[user_id]

                guilds_for_bot = [
                    {"id": g.id, "unavailable": True}
                    for g in WORLD.guilds.values()
                    if (user_id, g.id) in WORLD.members
                    and _shard_matches(session, g.id)
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
                    "shard": [session.shard_id, session.shard_count],
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

                for g in list(WORLD.guilds.values()):
                    if (user_id, g.id) in WORLD.members and _shard_matches(session, g.id):
                        await session.dispatch("GUILD_CREATE", g.to_dict(WORLD, full=True))

            elif op == OP_RESUME:
                d = msg.get("d") or {}
                req_session_id = d.get("session_id")
                req_token = d.get("token", "")
                if req_token.startswith("Bot "):
                    req_token = req_token[4:]
                req_seq = int(d.get("seq") or 0)

                _gc_resumables()
                saved = WORLD.resumable_sessions.get(req_session_id)
                if not saved or saved["token"] != req_token:
                    # Not resumable — tell client to re-identify.
                    await session.send({"op": OP_INVALID_SESSION, "d": False})
                    await ws.close(code=4009)
                    return

                # Adopt saved identity.
                session.session_id = req_session_id
                session.user_id = saved["user_id"]
                session.token = saved["token"]
                session.intents = saved["intents"]
                session.seq = saved["seq"]
                session.dispatch_history = deque(saved["history"], maxlen=RESUME_HISTORY_SIZE)
                WORLD.resumable_sessions.pop(req_session_id, None)

                await start_event_pump()

                # Replay dispatches after req_seq.
                for seq, ev_name, ev_data in list(session.dispatch_history):
                    if seq > req_seq:
                        await session.send(
                            {"op": OP_DISPATCH, "t": ev_name, "s": seq, "d": ev_data}
                        )

                # RESUMED marker (no data).
                await session.dispatch("RESUMED", {})

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

            elif op == OP_VOICE_STATE_UPDATE:
                d = msg.get("d") or {}
                if session.user_id and d.get("guild_id"):
                    vs = {
                        "guild_id": d["guild_id"],
                        "channel_id": d.get("channel_id"),
                        "user_id": session.user_id,
                        "session_id": session.session_id,
                        "deaf": False, "mute": False,
                        "self_deaf": d.get("self_deaf", False),
                        "self_mute": d.get("self_mute", False),
                        "self_video": False, "suppress": False,
                        "request_to_speak_timestamp": None,
                    }
                    WORLD.voice_states[(d["guild_id"], session.user_id)] = vs
                    WORLD.bus.publish("VOICE_STATE_UPDATE", vs)
                    # VOICE_SERVER_UPDATE (endpoint for WebRTC discovery)
                    WORLD.bus.publish("VOICE_SERVER_UPDATE", {
                        "token": f"voice.{session.session_id}",
                        "guild_id": d["guild_id"],
                        "endpoint": config.GATEWAY_WS_URL.replace("ws://", "").replace("wss://", "").split("/")[0] + "/voice",
                    })

            elif op == OP_REQUEST_GUILD_MEMBERS:
                d = msg.get("d") or {}
                guild_id = d.get("guild_id")
                guild_ids = guild_id if isinstance(guild_id, list) else [guild_id]
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
        # Save for RESUME.
        session.make_resumable()

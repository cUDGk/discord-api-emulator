"""Voice Gateway WebSocket.

Handles the control-plane dance (HELLO → IDENTIFY → READY → SELECT_PROTOCOL
→ SESSION_DESCRIPTION → heartbeats) and wires each session to the shared UDP
transport in `dapi_emu.voice.udp_server`. The READY payload advertises the
real UDP port the server bound, so discord.py's IP-discovery + RTP flow
actually gets responses.

The UDP side is a dumb passthrough SFU: packets from one SSRC are forwarded
to every other SSRC in the same voice room. Encryption is never touched.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..snowflake import generate as new_snowflake
from ..state import WORLD
from ..voice.udp_server import VoiceUDPServer, start_udp_server

log = logging.getLogger("dapi.voice")

router = APIRouter()

# Voice opcodes (Discord voice gateway v4)
VOP_IDENTIFY = 0
VOP_SELECT_PROTOCOL = 1
VOP_READY = 2
VOP_HEARTBEAT = 3
VOP_SESSION_DESCRIPTION = 4
VOP_SPEAKING = 5
VOP_HEARTBEAT_ACK = 6
VOP_RESUME = 7
VOP_HELLO = 8
VOP_RESUMED = 9
VOP_CLIENT_DISCONNECT = 13

# --- UDP server (lazy-initialised on first WS connection) ------------------
_udp_server: VoiceUDPServer | None = None
_udp_port: int = 0
_udp_lock = asyncio.Lock()
_udp_host = "127.0.0.1"


async def ensure_udp_started() -> tuple[VoiceUDPServer, int]:
    """Start the shared UDP server on first use and cache it.

    Idempotent: concurrent callers wait on the same lock and reuse the result.
    """
    global _udp_server, _udp_port
    if _udp_server is not None:
        return _udp_server, _udp_port
    async with _udp_lock:
        if _udp_server is None:
            _udp_server, _udp_port = await start_udp_server(_udp_host, 0)
            log.info("voice: UDP server bound on %s:%d", _udp_host, _udp_port)
    return _udp_server, _udp_port


def _new_ssrc() -> int:
    # 32-bit SSRC. Avoid 0; discord.py treats it as unset.
    while True:
        v = int.from_bytes(secrets.token_bytes(4), "big")
        if v == 0:
            continue
        if any(s.get("ssrc") == v for s in WORLD.voice_sessions.values()):
            continue
        return v


@router.websocket("/voice")
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    udp_server, udp_port = await ensure_udp_started()

    session_id = new_snowflake()
    ssrc = _new_ssrc()
    log.info("voice: new connection session=%s ssrc=%d", session_id, ssrc)

    # Voice Hello (op 8). Discord's real heartbeat_interval is ~13.75 s here.
    await ws.send_text(json.dumps({
        "op": VOP_HELLO,
        "d": {"heartbeat_interval": 13750},
    }))

    identified = False

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.close(code=4002)
                return
            op = msg.get("op")
            d = msg.get("d") or {}

            if op == VOP_IDENTIFY:
                # d: {server_id, user_id, session_id, token, video?}
                identified = True
                WORLD.voice_sessions[session_id] = {
                    "ssrc": ssrc,
                    "user_id": d.get("user_id"),
                    "guild_id": d.get("server_id"),
                    "channel_id": d.get("channel_id"),
                    "token": d.get("token"),
                    "remote_addr": None,
                }
                await ws.send_text(json.dumps({
                    "op": VOP_READY,
                    "d": {
                        "ssrc": ssrc,
                        "ip": _udp_host,
                        "port": udp_port,
                        "modes": [
                            "aead_aes256_gcm_rtpsize",
                            "aead_xchacha20_poly1305_rtpsize",
                            "xsalsa20_poly1305_lite_rtpsize",
                            "xsalsa20_poly1305_lite",
                            "xsalsa20_poly1305_suffix",
                            "xsalsa20_poly1305",
                        ],
                        "experiments": [],
                        # Deprecated — kept for legacy clients that still read it.
                        "heartbeat_interval": 1,
                    },
                }))

            elif op == VOP_HEARTBEAT:
                # Clients send their nonce, server echoes it in ACK.
                await ws.send_text(json.dumps({
                    "op": VOP_HEARTBEAT_ACK,
                    "d": d if isinstance(d, (int, float, str)) else 0,
                }))

            elif op == VOP_SELECT_PROTOCOL:
                # d: {protocol: "udp", data: {address, port, mode}}
                # The real binding happens when the client sends its first UDP
                # packet (IP discovery). Stash whatever the client *claims* its
                # address is in case we need it for diagnostics.
                pdata = d.get("data") or {}
                sess = WORLD.voice_sessions.get(session_id)
                if sess is not None:
                    sess["claimed_address"] = pdata.get("address")
                    sess["claimed_port"] = pdata.get("port")
                await ws.send_text(json.dumps({
                    "op": VOP_SESSION_DESCRIPTION,
                    "d": {
                        "mode": pdata.get("mode") or "xsalsa20_poly1305",
                        "secret_key": list(secrets.token_bytes(32)),
                        "audio_codec": "opus",
                        "video_codec": "H264",
                        "media_session_id": new_snowflake(),
                    },
                }))

            elif op == VOP_SPEAKING:
                # Echo back so the bot sees its own speaking state confirmed.
                d = dict(d) if isinstance(d, dict) else {}
                d.setdefault("ssrc", ssrc)
                d.setdefault("user_id", None)
                await ws.send_text(json.dumps({"op": VOP_SPEAKING, "d": d}))

            elif op == VOP_RESUME:
                await ws.send_text(json.dumps({"op": VOP_RESUMED, "d": None}))

            else:
                log.debug("voice: unhandled op %s", op)

    except WebSocketDisconnect:
        log.info("voice: disconnect %s", session_id)
    except Exception as e:
        log.exception("voice: error: %s", e)
    finally:
        # Tear down session bindings. UDP server itself stays up for other
        # concurrent sessions.
        WORLD.voice_sessions.pop(session_id, None)
        if _udp_server is not None:
            _udp_server.unbind_ssrc(ssrc)

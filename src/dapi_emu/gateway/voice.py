"""Voice Gateway WebSocket — protocol skeleton only.

Handles the control-plane dance (HELLO → IDENTIFY → READY → SELECT_PROTOCOL
→ SESSION_DESCRIPTION → heartbeats) so that discord.py / discord.js / JDA
voice clients don't crash. UDP audio transport is not implemented — the
`ip`/`port`/`ssrc` returned in READY point at localhost:0 and no RTP packets
are forwarded. This is enough to verify that a bot's voice.connect() pipeline
reaches "session description" state before it tries to send audio.
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


@router.websocket("/voice")
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    session_id = new_snowflake()
    ssrc = int.from_bytes(secrets.token_bytes(3), "big")  # 24-bit-ish ssrc
    log.info("voice: new connection session=%s", session_id)

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
                await ws.send_text(json.dumps({
                    "op": VOP_READY,
                    "d": {
                        "ssrc": ssrc,
                        "ip": "127.0.0.1",
                        "port": 0,  # no real UDP sink
                        "modes": [
                            "aead_aes256_gcm_rtpsize",
                            "aead_xchacha20_poly1305_rtpsize",
                            "xsalsa20_poly1305_lite_rtpsize",
                            "xsalsa20_poly1305_lite",
                            "xsalsa20_poly1305_suffix",
                            "xsalsa20_poly1305",
                        ],
                        "experiments": [],
                        "heartbeat_interval": 1,  # deprecated, kept for legacy clients
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
                await ws.send_text(json.dumps({
                    "op": VOP_SESSION_DESCRIPTION,
                    "d": {
                        "mode": (d.get("data") or {}).get("mode") or "xsalsa20_poly1305",
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

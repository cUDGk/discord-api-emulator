"""Voice gateway protocol skeleton tests.

Validates HELLO → IDENTIFY → READY → SELECT_PROTOCOL → SESSION_DESCRIPTION flow.
Audio UDP transport is out of scope.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from dapi_emu.app import create_app


def test_voice_handshake() -> None:
    c = TestClient(create_app())
    with c.websocket_connect("/voice") as ws:
        hello = ws.receive_json()
        assert hello["op"] == 8, f"expected HELLO, got {hello}"
        assert "heartbeat_interval" in hello["d"]

        ws.send_json({"op": 0, "d": {
            "server_id": "1", "user_id": "2", "session_id": "s", "token": "t",
        }})
        ready = ws.receive_json()
        assert ready["op"] == 2
        assert "ssrc" in ready["d"]
        assert ready["d"]["ip"] == "127.0.0.1"
        assert "modes" in ready["d"] and len(ready["d"]["modes"]) > 0

        ws.send_json({"op": 1, "d": {
            "protocol": "udp",
            "data": {"address": "127.0.0.1", "port": 40000,
                     "mode": "xsalsa20_poly1305"},
        }})
        sd = ws.receive_json()
        assert sd["op"] == 4
        assert sd["d"]["mode"] == "xsalsa20_poly1305"
        assert isinstance(sd["d"]["secret_key"], list)
        assert len(sd["d"]["secret_key"]) == 32

        # Heartbeat echo
        ws.send_json({"op": 3, "d": 12345})
        ack = ws.receive_json()
        assert ack["op"] == 6

        # Speaking echo
        ws.send_json({"op": 5, "d": {"speaking": 1, "delay": 0, "ssrc": ready["d"]["ssrc"]}})
        sp = ws.receive_json()
        assert sp["op"] == 5

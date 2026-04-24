from __future__ import annotations

import os

HOST = os.environ.get("DAPI_HOST", "127.0.0.1")
PORT = int(os.environ.get("DAPI_PORT", "8080"))
API_VERSION = 10
HEARTBEAT_INTERVAL_MS = 41250
GATEWAY_WS_URL = os.environ.get("DAPI_GATEWAY_URL", f"ws://{HOST}:{PORT}/gateway")
SELF_BASE_URL = os.environ.get("DAPI_BASE_URL", f"http://{HOST}:{PORT}")

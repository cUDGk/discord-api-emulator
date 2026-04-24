"""Tests for gateway ETF encoding, zstd-stream compression, and sharding."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# aiohttp on Windows refuses to start under the default Proactor loop.
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from dapi_emu.gateway.encoding import decode, encode, etf_pack, etf_unpack  # noqa: E402


# ---------------------------------------------------------------------------
# 1. ETF round-trip unit tests (pure-python fallback path)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    {},
    {"a": 1, "b": "two", "c": True, "d": None},
    [1, 2, 3, "four", 5.0],
    "hello world",
    0,
    255,
    256,
    -1,
    1234567,
    3.14159,
    True,
    False,
    None,
    {"nested": {"list": [1, {"x": [True, False, None]}, "end"]}},
])
def test_etf_roundtrip(value):
    packed = etf_pack(value)
    assert isinstance(packed, (bytes, bytearray))
    assert packed[0] == 131, "ETF must start with version marker 131"
    assert etf_unpack(packed) == value


def test_encode_decode_facade_json():
    payload = {"op": 10, "d": {"heartbeat_interval": 41250}}
    raw = encode(payload, "json")
    assert isinstance(raw, str)
    assert decode(raw, "json") == payload


def test_encode_decode_facade_etf():
    payload = {"op": 10, "d": {"heartbeat_interval": 41250}}
    raw = encode(payload, "etf")
    assert isinstance(raw, (bytes, bytearray))
    assert decode(raw, "etf") == payload


# ---------------------------------------------------------------------------
# 2. WebSocket integration: encoding=etf
# ---------------------------------------------------------------------------

def test_ws_etf_hello_decodes():
    """Connecting with encoding=etf must give us a binary HELLO frame we can
    decode with our own ETF parser."""
    from fastapi.testclient import TestClient

    from dapi_emu.app import create_app

    c = TestClient(create_app())
    with c.websocket_connect("/gateway?v=10&encoding=etf") as ws:
        raw = ws.receive_bytes()
        hello = etf_unpack(raw)
        assert hello["op"] == 10
        assert "heartbeat_interval" in hello["d"]


# ---------------------------------------------------------------------------
# 3. WebSocket integration: compress=zstd-stream
# ---------------------------------------------------------------------------

# Tests below need a real server subprocess because aiohttp drives the
# WebSocket — TestClient's sync WS shim is not suited for compress= flows that
# also exchange real-time dispatches.

BASE = os.environ.get("DAPI_BASE", "http://127.0.0.1:8090")
WS_URL = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/gateway"


def _server_ready(base: str) -> bool:
    try:
        httpx.get(base + "/", timeout=1.0)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def server():
    started = None
    if not _server_ready(BASE):
        env = os.environ.copy()
        # Different port from the other ws_concurrent module so they can coexist.
        env["DAPI_PORT"] = BASE.rsplit(":", 1)[-1]
        started = subprocess.Popen(
            [sys.executable, str(ROOT / "run.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(ROOT), env=env,
        )
        for _ in range(30):
            if _server_ready(BASE):
                break
            time.sleep(0.5)
        else:
            started.terminate()
            pytest.skip("dapi server did not start on " + BASE)
    yield BASE
    if started is not None:
        started.terminate()
        try:
            started.wait(timeout=5)
        except subprocess.TimeoutExpired:
            started.kill()


@pytest.mark.asyncio
async def test_zstd_stream_handshake(server: str):
    """compress=zstd-stream returns binary frames decodable by the zstandard lib."""
    try:
        import zstandard
    except ImportError:
        pytest.skip("zstandard not installed")

    import aiohttp

    httpx.post(f"{server}/admin/reset")
    bot = httpx.post(f"{server}/admin/users", json={"username": "zb", "bot": True}).json()
    owner = httpx.post(f"{server}/admin/users", json={"username": "ow"}).json()["user"]
    g = httpx.post(f"{server}/admin/guilds",
                   json={"name": "Z", "owner_id": owner["id"]}).json()
    httpx.post(f"{server}/admin/guilds/{g['id']}/members",
               json={"user_id": bot["user"]["id"]})

    decompressor = zstandard.ZstdDecompressor()
    frames: list[str] = []

    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS_URL + "?v=10&encoding=json&compress=zstd-stream") as ws:
            raw = await asyncio.wait_for(ws.receive(), timeout=5)
            assert raw.type is aiohttp.WSMsgType.BINARY
            hello = json.loads(decompressor.decompress(raw.data).decode("utf-8"))
            assert hello["op"] == 10
            frames.append("hello")

            await ws.send_json({
                "op": 2, "d": {
                    "token": bot["token"], "intents": 0,
                    "properties": {"os": "t", "browser": "t", "device": "t"},
                },
            })
            saw_ready = False
            for _ in range(4):
                try:
                    r = await asyncio.wait_for(ws.receive(), timeout=3)
                except asyncio.TimeoutError:
                    break
                if r.type is aiohttp.WSMsgType.BINARY:
                    text = decompressor.decompress(r.data).decode("utf-8")
                    if '"READY"' in text:
                        saw_ready = True
                        break
    assert saw_ready, "READY dispatch was not delivered over zstd-stream"


# ---------------------------------------------------------------------------
# 4. Sharding: each shard sees only its own guilds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sharding_partitions_guilds(server: str):
    """With shard_count=2 two sessions must between them see every guild
    exactly once. Each guild's owning shard is `(int(id) >> 22) % count`."""
    import aiohttp

    httpx.post(f"{server}/admin/reset")
    bot = httpx.post(f"{server}/admin/users",
                     json={"username": "sb", "bot": True}).json()
    owner = httpx.post(f"{server}/admin/users",
                      json={"username": "so"}).json()["user"]

    # Spin up guilds until we have at least one in each of two shard buckets.
    # With time-based snowflakes this happens within a handful of attempts.
    guilds: list[dict] = []
    for _ in range(40):
        g = httpx.post(f"{server}/admin/guilds",
                       json={"name": f"G{len(guilds)}", "owner_id": owner["id"]}).json()
        httpx.post(f"{server}/admin/guilds/{g['id']}/members",
                   json={"user_id": bot["user"]["id"]})
        guilds.append(g)
        buckets = {(int(x["id"]) >> 22) % 2 for x in guilds}
        if buckets == {0, 1} and len(guilds) >= 2:
            break
        time.sleep(0.005)
    assert {(int(g["id"]) >> 22) % 2 for g in guilds} == {0, 1}, (
        f"failed to land guilds on both shards: {[g['id'] for g in guilds]}"
    )

    expected: dict[int, set[str]] = {0: set(), 1: set()}
    for g in guilds:
        expected[(int(g["id"]) >> 22) % 2].add(g["id"])

    async def collect_for_shard(shard_id: int) -> set[str]:
        seen: set[str] = set()
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(WS_URL + "?v=10&encoding=json") as ws:
                hello = await asyncio.wait_for(ws.receive(), timeout=5)
                assert json.loads(hello.data)["op"] == 10
                await ws.send_json({
                    "op": 2, "d": {
                        "token": bot["token"], "intents": 0,
                        "shard": [shard_id, 2],
                        "properties": {"os": "t", "browser": "t", "device": "t"},
                    },
                })
                deadline = time.time() + 4.0
                while time.time() < deadline:
                    try:
                        r = await asyncio.wait_for(ws.receive(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if r.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    msg = json.loads(r.data)
                    if msg.get("t") == "GUILD_CREATE":
                        seen.add(msg["d"]["id"])
                    elif msg.get("t") == "READY":
                        # READY's guilds list is also shard-filtered; record it.
                        for g in msg["d"].get("guilds", []):
                            seen.add(g["id"])
        return seen

    seen0, seen1 = await asyncio.gather(
        collect_for_shard(0),
        collect_for_shard(1),
    )

    assert seen0 == expected[0], f"shard 0 mismatch: got {seen0}, expected {expected[0]}"
    assert seen1 == expected[1], f"shard 1 mismatch: got {seen1}, expected {expected[1]}"
    assert seen0.isdisjoint(seen1), "guild leaked across shards"

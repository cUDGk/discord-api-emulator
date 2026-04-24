"""実サーバー起動 + aiohttp で 2本の WS を並行で張って、混線しない事を検証。

前提: `python run.py` がローカルで稼働している事。CI で走らせる前に
`(python run.py > /tmp/dapi_server.log 2>&1 &)` を呼び出すフィクスチャも兼ねる。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# aiohttp on Windows needs the selector loop; aiodns blows up on Proactor.
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import aiohttp
import httpx
import pytest


BASE = os.environ.get("DAPI_BASE", "http://127.0.0.1:8080")
WS = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/gateway"


def _server_ready() -> bool:
    try:
        httpx.get(BASE + "/", timeout=1.0)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def server():
    started = None
    if not _server_ready():
        # Start a fresh server in the background for the duration of this module.
        root = Path(__file__).parent.parent
        started = subprocess.Popen(
            [sys.executable, str(root / "run.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(root),
        )
        for _ in range(20):
            if _server_ready():
                break
            time.sleep(0.5)
    yield BASE
    if started is not None:
        started.terminate()
        try:
            started.wait(timeout=5)
        except subprocess.TimeoutExpired:
            started.kill()


async def _identify_and_collect(token: str, intents: int, *, duration: float) -> list[dict]:
    """Connect, identify, collect dispatches for `duration` seconds, then close."""
    events: list[dict] = []
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS + "?v=10&encoding=json") as ws:
            # Read Hello
            msg = await ws.receive(timeout=5)
            hello = json.loads(msg.data)
            assert hello["op"] == 10

            await ws.send_json({
                "op": 2, "d": {
                    "token": token, "intents": intents,
                    "properties": {"os": "test", "browser": "test", "device": "test"},
                },
            })

            end_at = time.time() + duration
            while time.time() < end_at:
                remaining = end_at - time.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.receive(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if raw.type is aiohttp.WSMsgType.TEXT:
                    events.append(json.loads(raw.data))
                elif raw.type is aiohttp.WSMsgType.BINARY:
                    # zlib-stream disabled for these tests (no compress= query)
                    pass
                elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                    break
    return events


@pytest.mark.asyncio
async def test_two_bots_do_not_cross_talk(server: str) -> None:
    """Two bots in disjoint guilds. A message posted in guild A must not reach bot B."""
    # Reset world and set up two independent (bot, guild) pairs.
    httpx.post(f"{BASE}/admin/reset")

    def user(name: str, bot: bool = False) -> dict:
        return httpx.post(f"{BASE}/admin/users", json={"username": name, "bot": bot}).json()

    ownerA = user("ownerA")["user"]
    ownerB = user("ownerB")["user"]
    botA_resp = user("botA", bot=True)
    botB_resp = user("botB", bot=True)

    gA = httpx.post(f"{BASE}/admin/guilds", json={"name": "GA", "owner_id": ownerA["id"]}).json()
    gB = httpx.post(f"{BASE}/admin/guilds", json={"name": "GB", "owner_id": ownerB["id"]}).json()
    httpx.post(f"{BASE}/admin/guilds/{gA['id']}/members", json={"user_id": botA_resp["user"]["id"]})
    httpx.post(f"{BASE}/admin/guilds/{gB['id']}/members", json={"user_id": botB_resp["user"]["id"]})

    chA = gA["channels"][0]["id"]

    MESSAGE_CONTENT = 1 << 15
    GUILD_MESSAGES = 1 << 9
    GUILDS = 1 << 0
    intents = GUILDS | GUILD_MESSAGES | MESSAGE_CONTENT

    # Start both bots, inject after they're ready.
    async def runner(token: str, duration: float) -> list[dict]:
        return await _identify_and_collect(token, intents, duration=duration)

    async def inject_after(delay: float) -> None:
        await asyncio.sleep(delay)
        httpx.post(f"{BASE}/admin/send-message", json={
            "author_id": ownerA["id"], "channel_id": chA,
            "content": "hello in A",
        })

    evA, evB, _ = await asyncio.gather(
        runner(botA_resp["token"], 3.0),
        runner(botB_resp["token"], 3.0),
        inject_after(1.2),
    )

    def contents(events: list[dict]) -> list[str]:
        return [e["d"]["content"] for e in events
                if e.get("t") == "MESSAGE_CREATE" and "content" in (e.get("d") or {})]

    a_msgs = contents(evA)
    b_msgs = contents(evB)

    assert "hello in A" in a_msgs, f"botA should have received its guild's message; got {a_msgs}"
    assert "hello in A" not in b_msgs, f"LEAK: botB saw guild A's message: {b_msgs}"


@pytest.mark.asyncio
async def test_zlib_stream_handshake(server: str) -> None:
    """Connecting with compress=zlib-stream returns binary frames that decompress
    correctly. If this fails, discord.js (which requires compress by default)
    won't be able to connect."""
    import zlib

    # Make sure a bot exists to identify with.
    httpx.post(f"{BASE}/admin/reset")
    bot = httpx.post(f"{BASE}/admin/users", json={"username": "zbot", "bot": True}).json()
    owner = httpx.post(f"{BASE}/admin/users", json={"username": "o"}).json()["user"]
    g = httpx.post(f"{BASE}/admin/guilds", json={"name": "Z", "owner_id": owner["id"]}).json()
    httpx.post(f"{BASE}/admin/guilds/{g['id']}/members", json={"user_id": bot["user"]["id"]})

    decomp = zlib.decompressobj()
    got_text: list[str] = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS + "?v=10&encoding=json&compress=zlib-stream") as ws:
            # Hello (first frame)
            raw = await asyncio.wait_for(ws.receive(), timeout=5)
            assert raw.type is aiohttp.WSMsgType.BINARY, f"expected binary, got {raw.type}"
            hello_text = decomp.decompress(raw.data).decode("utf-8")
            got_text.append(hello_text)
            hello = json.loads(hello_text)
            assert hello["op"] == 10

            await ws.send_json({
                "op": 2, "d": {
                    "token": bot["token"], "intents": 0,
                    "properties": {"os": "t", "browser": "t", "device": "t"},
                },
            })
            # READY + GUILD_CREATE: drain up to 2 frames
            for _ in range(2):
                try:
                    r = await asyncio.wait_for(ws.receive(), timeout=3)
                except asyncio.TimeoutError:
                    break
                if r.type is aiohttp.WSMsgType.BINARY:
                    got_text.append(decomp.decompress(r.data).decode("utf-8"))
    # Must see at least a READY dispatch
    assert any('"READY"' in t for t in got_text), f"no READY decoded; got {got_text!r}"

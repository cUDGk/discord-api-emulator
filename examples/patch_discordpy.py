"""discord.py を dapi-emu に向ける monkey patch。

使い方:
    import patch_discordpy
    patch_discordpy.apply()
    # この後に discord を import して通常通り使う

差し替え対象:
    * discord.http.Route.BASE                         -> REST base URL
    * discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY -> WebSocket URL
    * discord.http.HTTPClient.get_gateway / get_bot_gateway
      (TLS検証や本物の gateway.discord.gg への問い合わせを回避)
"""
from __future__ import annotations

import yarl

# エミュレーター接続先（必要なら環境変数で上書きできるようにしてもいい）
EMULATOR_HTTP_BASE = "http://127.0.0.1:8080/api/v10"
EMULATOR_WS_URL = "ws://127.0.0.1:8080/gateway"

_applied = False


def apply() -> None:
    """discord.py の接続先をエミュレーターに差し替える。

    複数回呼んでも安全（_applied フラグで no-op 化）。
    """
    global _applied
    if _applied:
        return

    # Windows で aiohttp + aiodns が ProactorEventLoop だと落ちるので
    # SelectorEventLoop に切り替える。
    import sys as _sys
    if _sys.platform == "win32":
        import asyncio as _asyncio
        try:
            _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

    import discord.http
    import discord.gateway

    # --- REST base URL 差し替え ---
    # Route.BASE はクラス変数。以降に生成される全 Route に効く。
    discord.http.Route.BASE = EMULATOR_HTTP_BASE

    # --- Gateway URL 差し替え ---
    # DEFAULT_GATEWAY は yarl.URL で持っている
    discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY = yarl.URL(EMULATOR_WS_URL)

    # --- HTTPClient.get_gateway / get_bot_gateway を差し替え ---
    # 本来は https://discord.com/api/v10/gateway(/bot) を叩いて
    # wss://gateway.discord.gg/... を返す。エミュレーター相手では
    # TLS 回避のため固定値を返したい。
    async def _get_gateway(self, *, encoding: str = "json", zlib: bool = True) -> str:
        # v=10 & encoding 付きで返すのが discord.py の期待する形
        if zlib:
            return f"{EMULATOR_WS_URL}?encoding={encoding}&v=10&compress=zlib-stream"
        return f"{EMULATOR_WS_URL}?encoding={encoding}&v=10"

    async def _get_bot_gateway(self, *, encoding: str = "json", zlib: bool = True):
        url = await _get_gateway(self, encoding=encoding, zlib=zlib)
        # discord.py 2.x では (shard_count, gateway_payload) タプルを返す
        return (
            1,
            {
                "url": url,
                "shards": 1,
                "session_start_limit": {
                    "total": 1000,
                    "remaining": 1000,
                    "reset_after": 0,
                    "max_concurrency": 1,
                },
            },
        )

    discord.http.HTTPClient.get_gateway = _get_gateway
    discord.http.HTTPClient.get_bot_gateway = _get_bot_gateway

    _applied = True
    print(f"[patch_discordpy] REST  -> {EMULATOR_HTTP_BASE}")
    print(f"[patch_discordpy] WS    -> {EMULATOR_WS_URL}")

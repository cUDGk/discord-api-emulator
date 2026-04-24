"""dapi-emu に初期データを流し込むセットアップスクリプト。

やる事:
    1. owner ユーザーを作成（bot=false）
    2. bot ユーザーを作成（bot=true）-> token もここで貰う
    3. guild を作成（owner を owner_id に指定）
    4. bot を guild のメンバーに追加
    5. guild 作成時に自動生成された #general の channel id を state snapshot から拾う
    6. 結果を画面表示 & .env に書き出し

標準ライブラリのみで動く（urllib）。
"""
from __future__ import annotations

import json
import os
import sys
from urllib import request, error

EMULATOR_BASE = os.environ.get("DAPI_EMULATOR_BASE", "http://127.0.0.1:8080")
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{EMULATOR_BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as e:
        sys.stderr.write(
            f"HTTP {e.code} on {method} {url}:\n{e.read().decode('utf-8', 'replace')}\n"
        )
        raise
    except error.URLError as e:
        sys.stderr.write(
            f"エミュレーターに接続できません ({url}): {e}\n"
            f"先に `python run.py` を起動していますか?\n"
        )
        raise SystemExit(1)
    if not body:
        return {}
    return json.loads(body)


def _find_general_channel(guild_id: str) -> str | None:
    """/admin/state から guild の #general を探す。"""
    state = _request("GET", "/admin/state")
    # state の形は実装依存。よくある形を順に試す。
    channels = state.get("channels") or []
    if isinstance(channels, dict):
        channels = list(channels.values())
    for ch in channels:
        if str(ch.get("guild_id")) != str(guild_id):
            continue
        if ch.get("name") == "general":
            return str(ch["id"])
    # 見つからなければ guild 配下の最初の text channel にフォールバック
    for ch in channels:
        if str(ch.get("guild_id")) == str(guild_id):
            return str(ch["id"])
    return None


def main() -> None:
    print(f"Emulator: {EMULATOR_BASE}")

    # 1. owner user
    owner_resp = _request(
        "POST", "/admin/users", {"username": "owner", "bot": False}
    )
    owner = owner_resp.get("user", owner_resp)
    owner_id = str(owner["id"])
    print(f"  owner user id = {owner_id}")

    # 2. bot user
    bot_resp = _request(
        "POST", "/admin/users", {"username": "example-bot", "bot": True}
    )
    bot_user = bot_resp.get("user", {})
    bot_user_id = str(bot_user.get("id") or bot_resp.get("id"))
    bot_token = bot_resp.get("token")
    application_id = bot_resp.get("application_id")
    if not bot_token:
        sys.stderr.write("エミュレーターが token を返しませんでした。\n")
        sys.exit(1)
    print(f"  bot user id   = {bot_user_id}")
    print(f"  application_id = {application_id}")

    # 3. guild
    guild_resp = _request(
        "POST", "/admin/guilds", {"name": "Test Guild", "owner_id": owner_id}
    )
    guild = guild_resp.get("guild", guild_resp)
    guild_id = str(guild["id"])
    print(f"  guild id      = {guild_id}")

    # 4. bot を guild メンバーに追加
    _request(
        "POST",
        f"/admin/guilds/{guild_id}/members",
        {"user_id": bot_user_id},
    )
    print("  bot joined guild")

    # 5. #general を拾う
    channel_id = _find_general_channel(guild_id)
    if channel_id is None:
        sys.stderr.write("警告: #general チャンネルが見つかりませんでした。\n")
        channel_id = ""
    print(f"  channel id    = {channel_id}")

    # 6. 画面 & .env
    print()
    print("=== Result ===")
    print(f"DAPI_BOT_TOKEN={bot_token}")
    print(f"GUILD_ID={guild_id}")
    print(f"CHANNEL_ID={channel_id}")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(f"DAPI_BOT_TOKEN={bot_token}\n")
        f.write(f"GUILD_ID={guild_id}\n")
        f.write(f"CHANNEL_ID={channel_id}\n")
    print(f"\n.env を書き出しました: {ENV_PATH}")


if __name__ == "__main__":
    main()

"""dapi-emu につなぐシンプルな discord.py bot のサンプル。

実行:
    export DAPI_BOT_TOKEN=...    # もしくは setup.py が書いた .env を読む
    python examples/simple_bot.py
"""
from __future__ import annotations

# ---- 重要: discord を import する前にパッチを当てる ----
import patch_discordpy

patch_discordpy.apply()

import os
import sys

import discord
from discord import app_commands

# .env があれば簡易パース（python-dotenv への依存は増やさない）
def _load_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

TOKEN = os.environ.get("DAPI_BOT_TOKEN") or os.environ.get("TOKEN")
if not TOKEN:
    sys.stderr.write(
        "ERROR: DAPI_BOT_TOKEN (or TOKEN) が設定されていません。\n"
        "  `python examples/setup.py` を先に実行して .env を生成してください。\n"
    )
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True


class SimpleBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        # slash コマンドをグローバル同期
        await self.tree.sync()


client = SimpleBot()


@client.event
async def on_ready() -> None:
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    # 自分のメッセージは無視（ループ防止）
    if message.author == client.user:
        return
    if message.content == "!ping":
        await message.channel.send("pong!")


@client.tree.command(name="hello", description="挨拶する")
async def hello(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"Hi {interaction.user.name}!")


if __name__ == "__main__":
    client.run(TOKEN)

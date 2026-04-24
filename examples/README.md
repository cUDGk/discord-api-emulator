# dapi-emu x discord.py サンプル

`discord.py` (2.x) をエミュレーターに繋ぐ monkey patch と、最小構成の bot サンプル。

## 3ステップで動かす

### 1. エミュレーター起動

リポジトリルートで:

```bash
python run.py
```

`http://127.0.0.1:8080` で待ち受ける想定。

### 2. 初期データ作成

別ターミナルで:

```bash
python examples/setup.py
```

owner ユーザー / bot ユーザー / guild / #general チャンネルを作成し、
bot token と id を `examples/.env` に書き出す。

### 3. bot 起動

```bash
python examples/simple_bot.py
```

`.env` から `DAPI_BOT_TOKEN` を読む。環境変数 `DAPI_BOT_TOKEN` or `TOKEN` でも可。

## 動作

- `!ping` と送ると `pong!` を返す
- slash コマンド `/hello` で `Hi {user.name}!` を返す

## ファイル

- `patch_discordpy.py` — `discord.http.Route.BASE` / `DiscordWebSocket.DEFAULT_GATEWAY` /
  `HTTPClient.get_gateway` を差し替える。`apply()` を呼ぶだけで有効化。
- `simple_bot.py` — `patch_discordpy.apply()` を先頭で呼ぶ最小 bot。
- `setup.py` — エミュレーターの `/admin/*` API を叩いて初期データを作る。

## 注意

- `patch_discordpy.apply()` は **必ず `import discord` より前** に呼ぶこと
  （`Route.BASE` はクラス変数なのでインスタンス生成前に差し替える必要あり）。
- エミュレーター側の TLS 検証は回避済み（平文 http/ws 前提）。
- エミュレーターのホスト/ポートを変える場合は `patch_discordpy.py` 冒頭の
  `EMULATOR_HTTP_BASE` / `EMULATOR_WS_URL` を書き換えるか、`setup.py` は
  環境変数 `DAPI_EMULATOR_BASE` で上書き可能。

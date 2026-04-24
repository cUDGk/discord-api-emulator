# dapi-emu — Discord Bot API Emulator

完全オフラインで動作する Discord Bot API のエミュレーター。`discord.py` / `discord.js` などの実 bot ライブラリがそのまま繋がるのをゴールにする。

## 特徴
- REST API (v10 互換) + WebSocket Gateway の両実装
- ユーザー/ギルド/チャンネル/ロール/メンバーを自由に作成してテスト
- ブラウザの**テストコンパネ** (`http://localhost:8080/panel`) からイベント注入
- in-memory / SQLite 切替可能なストレージ層
- オフライン完結（CDN/OAuth2 の外部リダイレクトを除く）

## 起動

```bash
pip install -r requirements.txt
python run.py
```

起動後：
- REST: `http://localhost:8080/api/v10/...`
- Gateway WS: `ws://localhost:8080/gateway`
- テストコンパネ: `http://localhost:8080/panel`

## Bot からの接続例 (discord.py)

```python
import discord
import os

# discord.py は HTTP ベース URL を差し替えられないので、monkey patch が必要
# （後述の docs/connecting-discordpy.md 参照）
```

## 実装フェーズ
| Phase | 内容 | 状態 |
|---|---|---|
| 1 | Gateway + 基本REST | **進行中** |
| 2 | Messages 完全版 | |
| 3 | Guild/Channel/Role/Member CRUD | |
| 4 | Interactions | |
| 5 | Threads/Webhooks/Stickers | |
| 6 | テストコンパネ拡充 | |
| 7 | OAuth2 / CDN / Sharding | |
| 8 | Voice Gateway + WebRTC | |

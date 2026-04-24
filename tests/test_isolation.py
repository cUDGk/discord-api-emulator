"""複数 bot 接続時のイベント混線が起きないことを確認するテスト。

Gateway の `_should_deliver` フィルタを直接叩いて、各 bot セッションが
自分の所属ギルド・DM・interaction のイベントしか受け取らない事を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dapi_emu.state import WORLD
from dapi_emu.gateway.server import _should_deliver


@dataclass
class FakeSession:
    user_id: str


def _reset() -> None:
    WORLD.users.clear(); WORLD.guilds.clear(); WORLD.channels.clear()
    WORLD.messages.clear(); WORLD.channel_messages.clear()
    WORLD.roles.clear(); WORLD.members.clear()
    WORLD.applications.clear(); WORLD.bot_tokens.clear()


def test_message_create_isolated_by_guild() -> None:
    _reset()
    botA = WORLD.create_user("botA", bot=True); appA, _ = WORLD.register_bot(botA)
    botB = WORLD.create_user("botB", bot=True); _appB, _ = WORLD.register_bot(botB)
    ownerA = WORLD.create_user("ownerA"); ownerB = WORLD.create_user("ownerB")
    gA = WORLD.create_guild("A", ownerA.id); gB = WORLD.create_guild("B", ownerB.id)
    WORLD.add_member(botA.id, gA.id); WORLD.add_member(botB.id, gB.id)
    chA = WORLD.create_channel("general", guild_id=gA.id)
    chB = WORLD.create_channel("general", guild_id=gB.id)

    sA = FakeSession(user_id=botA.id); sB = FakeSession(user_id=botB.id)

    ev_in_A = {"id": "1", "channel_id": chA.id, "guild_id": gA.id,
               "author": {"id": ownerA.id}, "content": "hi"}
    assert _should_deliver(sA, "MESSAGE_CREATE", ev_in_A) is True
    assert _should_deliver(sB, "MESSAGE_CREATE", ev_in_A) is False, "leak to bot B!"

    ev_in_B = {"id": "2", "channel_id": chB.id, "guild_id": gB.id,
               "author": {"id": ownerB.id}, "content": "hi"}
    assert _should_deliver(sB, "MESSAGE_CREATE", ev_in_B) is True
    assert _should_deliver(sA, "MESSAGE_CREATE", ev_in_B) is False


def test_guild_scoped_events_isolated() -> None:
    _reset()
    botA = WORLD.create_user("botA", bot=True); WORLD.register_bot(botA)
    botB = WORLD.create_user("botB", bot=True); WORLD.register_bot(botB)
    owner = WORLD.create_user("owner")
    gA = WORLD.create_guild("A", owner.id); gB = WORLD.create_guild("B", owner.id)
    WORLD.add_member(botA.id, gA.id); WORLD.add_member(botB.id, gB.id)

    sA = FakeSession(botA.id); sB = FakeSession(botB.id)

    ev = {"guild_id": gA.id, "role": {"id": "x"}}
    assert _should_deliver(sA, "GUILD_ROLE_CREATE", ev)
    assert not _should_deliver(sB, "GUILD_ROLE_CREATE", ev)


def test_interaction_isolated_by_application() -> None:
    _reset()
    botA = WORLD.create_user("botA", bot=True); appA, _ = WORLD.register_bot(botA)
    botB = WORLD.create_user("botB", bot=True); appB, _ = WORLD.register_bot(botB)
    owner = WORLD.create_user("owner")
    g = WORLD.create_guild("G", owner.id)
    WORLD.add_member(botA.id, g.id); WORLD.add_member(botB.id, g.id)

    sA = FakeSession(botA.id); sB = FakeSession(botB.id)

    ev = {"id": "i1", "application_id": appA.id, "type": 2,
          "channel_id": g.channel_ids[0] if g.channel_ids else None, "guild_id": g.id,
          "data": {"name": "ping"}}
    assert _should_deliver(sA, "INTERACTION_CREATE", ev) is True
    assert _should_deliver(sB, "INTERACTION_CREATE", ev) is False, "interaction leak!"


def test_dm_isolated_by_recipients() -> None:
    _reset()
    botA = WORLD.create_user("botA", bot=True); WORLD.register_bot(botA)
    botB = WORLD.create_user("botB", bot=True); WORLD.register_bot(botB)
    alice = WORLD.create_user("alice")

    dm = WORLD.create_channel("", type=1, guild_id=None)
    dm.recipients = [botA.id, alice.id]

    sA = FakeSession(botA.id); sB = FakeSession(botB.id)

    ev = {"id": "1", "channel_id": dm.id, "author": {"id": alice.id}, "content": "hey"}
    assert _should_deliver(sA, "MESSAGE_CREATE", ev) is True
    assert _should_deliver(sB, "MESSAGE_CREATE", ev) is False, "DM leak!"


def test_broadcast_events_always_delivered() -> None:
    _reset()
    botA = WORLD.create_user("botA", bot=True); WORLD.register_bot(botA)
    sA = FakeSession(botA.id)
    assert _should_deliver(sA, "READY", {})
    assert _should_deliver(sA, "RESUMED", {})
    assert _should_deliver(sA, "USER_UPDATE", {"id": botA.id})

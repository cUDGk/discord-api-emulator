"""SQLite persistence round-trip tests."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from dapi_emu import persistence
from dapi_emu.state import (
    Application,
    Channel,
    Guild,
    Member,
    Message,
    Role,
    User,
    World,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "dapi.db")


def _seed(world: World) -> dict:
    """Populate a world with one of everything interesting and return the
    pieces we want to assert on."""
    owner = world.create_user("owner")
    bot = world.create_user("mybot", bot=True)
    app, token = world.register_bot(bot)
    guild = world.create_guild("Guild One", owner.id)
    channel = world.create_channel("general", type=0, guild_id=guild.id)
    role = world.create_role(guild.id, "moderator", color=0xFF0000, hoist=True)
    world.add_member(bot.id, guild.id, nick="Bottington")
    msg = world.create_message(channel.id, owner.id, "hello world")

    # tuple-keyed buckets
    world.voice_states[(guild.id, owner.id)] = {
        "user_id": owner.id,
        "channel_id": channel.id,
        "session_id": "abc",
        "deaf": False,
        "mute": False,
    }
    world.bans[(guild.id, bot.id)] = {"reason": "test", "user": bot.to_dict()}

    # plain dict bucket
    world.webhooks["wh1"] = {"id": "wh1", "name": "hook", "channel_id": channel.id}
    world.channel_overwrites[channel.id] = [{"id": role.id, "type": 0, "allow": "0", "deny": "0"}]

    return {
        "owner_id": owner.id,
        "bot_id": bot.id,
        "app_id": app.id,
        "token": token,
        "guild_id": guild.id,
        "channel_id": channel.id,
        "role_id": role.id,
        "msg_id": msg.id,
    }


def test_is_enabled_env_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAPI_DB_PATH", raising=False)
    assert persistence.is_enabled() is False
    monkeypatch.setenv("DAPI_DB_PATH", "")
    assert persistence.is_enabled() is False
    monkeypatch.setenv("DAPI_DB_PATH", "/tmp/x.db")
    assert persistence.is_enabled() is True


def test_load_from_missing_file_is_noop(db_path: str) -> None:
    w = World()
    w.create_user("alice")
    # File doesn't exist — load_world should do nothing, not crash.
    persistence.load_world(w, db_path)
    assert "alice" in {u.username for u in w.users.values()}


def test_load_from_empty_db_is_noop(db_path: str) -> None:
    persistence.init_db(db_path)  # creates empty schema
    w = World()
    w.create_user("alice")
    persistence.load_world(w, db_path)
    assert "alice" in {u.username for u in w.users.values()}


def test_save_and_load_roundtrip(db_path: str) -> None:
    src = World()
    ids = _seed(src)

    persistence.save_world(src, db_path)

    dst = World()
    persistence.load_world(dst, db_path)

    # Users
    assert ids["owner_id"] in dst.users
    assert ids["bot_id"] in dst.users
    assert isinstance(dst.users[ids["owner_id"]], User)
    assert dst.users[ids["owner_id"]].username == "owner"
    assert dst.users[ids["bot_id"]].bot is True

    # Guild
    assert ids["guild_id"] in dst.guilds
    assert isinstance(dst.guilds[ids["guild_id"]], Guild)
    assert dst.guilds[ids["guild_id"]].owner_id == ids["owner_id"]
    # Guild has @everyone (id == guild_id) + moderator role
    assert ids["role_id"] in dst.guilds[ids["guild_id"]].role_ids

    # Channel
    assert ids["channel_id"] in dst.channels
    assert isinstance(dst.channels[ids["channel_id"]], Channel)
    assert dst.channels[ids["channel_id"]].name == "general"

    # Role
    assert ids["role_id"] in dst.roles
    assert isinstance(dst.roles[ids["role_id"]], Role)
    assert dst.roles[ids["role_id"]].color == 0xFF0000
    assert dst.roles[ids["role_id"]].hoist is True

    # Message
    assert ids["msg_id"] in dst.messages
    assert isinstance(dst.messages[ids["msg_id"]], Message)
    assert dst.messages[ids["msg_id"]].content == "hello world"
    assert dst.channel_messages[ids["channel_id"]] == [ids["msg_id"]]

    # Application + tokens (plain dict)
    assert ids["app_id"] in dst.applications
    assert isinstance(dst.applications[ids["app_id"]], Application)
    assert dst.bot_tokens[ids["token"]] == ids["bot_id"]


def test_tuple_key_buckets_survive(db_path: str) -> None:
    src = World()
    ids = _seed(src)
    persistence.save_world(src, db_path)

    dst = World()
    persistence.load_world(dst, db_path)

    # members: (user_id, guild_id)
    member_key = (ids["bot_id"], ids["guild_id"])
    assert member_key in dst.members
    assert isinstance(dst.members[member_key], Member)
    assert dst.members[member_key].nick == "Bottington"

    # voice_states: (guild_id, user_id)
    vs_key = (ids["guild_id"], ids["owner_id"])
    assert vs_key in dst.voice_states
    assert dst.voice_states[vs_key]["channel_id"] == ids["channel_id"]

    # bans: (guild_id, user_id)
    ban_key = (ids["guild_id"], ids["bot_id"])
    assert ban_key in dst.bans
    assert dst.bans[ban_key]["reason"] == "test"


def test_wipe_db_clears_everything(db_path: str) -> None:
    src = World()
    _seed(src)
    persistence.save_world(src, db_path)

    persistence.wipe_db(db_path)

    dst = World()
    persistence.load_world(dst, db_path)
    # Nothing should have been restored; dst is whatever a fresh World is.
    assert dst.users == {}
    assert dst.guilds == {}
    assert dst.messages == {}


def test_wipe_db_on_missing_file_is_noop(tmp_path: Path) -> None:
    # Must not crash when the DB hasn't been created yet.
    persistence.wipe_db(str(tmp_path / "never.db"))


def test_save_overwrites_previous_snapshot(db_path: str) -> None:
    src = World()
    u1 = src.create_user("first")
    persistence.save_world(src, db_path)

    # Mutate: drop first user, add a different one
    src.users.pop(u1.id)
    u2 = src.create_user("second")
    persistence.save_world(src, db_path)

    dst = World()
    persistence.load_world(dst, db_path)
    assert u1.id not in dst.users
    assert u2.id in dst.users
    assert dst.users[u2.id].username == "second"


async def test_autosave_flushes_periodically(db_path: str) -> None:
    import asyncio

    world = World()
    task = await persistence.start_autosave(world, db_path, interval_sec=0.05)
    try:
        world.create_user("persisted")
        await asyncio.sleep(0.2)  # a few autosave ticks
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    dst = World()
    persistence.load_world(dst, db_path)
    assert any(u.username == "persisted" for u in dst.users.values())


def test_load_preserves_dataclass_types(db_path: str) -> None:
    """Regression: JSON round-trip must not leave plain dicts where the World
    expects dataclass instances."""
    src = World()
    _seed(src)
    persistence.save_world(src, db_path)

    dst = World()
    persistence.load_world(dst, db_path)

    for u in dst.users.values():
        assert isinstance(u, User)
    for g in dst.guilds.values():
        assert isinstance(g, Guild)
    for c in dst.channels.values():
        assert isinstance(c, Channel)
    for m in dst.messages.values():
        assert isinstance(m, Message)
    for r in dst.roles.values():
        assert isinstance(r, Role)
    for mem in dst.members.values():
        assert isinstance(mem, Member)
    for a in dst.applications.values():
        assert isinstance(a, Application)

"""SQLite persistence layer for WORLD.

Ephemeral mode (DAPI_DB_PATH unset or empty) keeps the emulator fully
in-memory, identical to the original behavior. When a path is configured,
the world is restored on boot and a background task flushes the full
snapshot every few seconds (simple wipe-and-rewrite — the world is small).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .state import Application, Channel, Guild, Member, Message, Role, User, World

log = logging.getLogger(__name__)


# --- bucket <-> dataclass mapping ----------------------------------------

# dict[key, Dataclass] buckets. Everything else is treated as plain JSON.
_DATACLASS_BUCKETS: dict[str, type] = {
    "users": User,
    "guilds": Guild,
    "channels": Channel,
    "messages": Message,
    "roles": Role,
    "members": Member,
    "applications": Application,
}

# All WORLD fields we persist. Order matters only for readability.
_BUCKETS: tuple[str, ...] = (
    "users",
    "guilds",
    "channels",
    "messages",
    "channel_messages",
    "roles",
    "members",
    "applications",
    "bot_tokens",
    "commands",
    "guild_commands",
    "global_commands",
    "threads",
    "thread_members",
    "webhooks",
    "invites",
    "guild_invites",
    "emojis",
    "guild_emojis",
    "stickers",
    "guild_stickers",
    "sticker_packs",
    "bans",
    "audit_logs",
    "voice_states",
    "stage_instances",
    "scheduled_events",
    "guild_scheduled_events",
    "automod_rules",
    "guild_automod_rules",
    "polls",
    "entitlements",
    "skus",
    "teams",
    "oauth_authorizations",
    "oauth_tokens",
    "pinned_messages",
    "interaction_tokens",
    "channel_overwrites",
    "resumable_sessions",
    "voice_sessions",
)

# Buckets whose keys are tuples. Stored as "a:b" in SQLite, restored via split.
_TUPLE_KEY_BUCKETS: frozenset[str] = frozenset({
    "members",
    "guild_commands",
    "thread_members",
    "bans",
    "voice_states",
})


# --- env / path ----------------------------------------------------------

_ENV_VAR = "DAPI_DB_PATH"


def is_enabled() -> bool:
    """True iff DAPI_DB_PATH is set to a non-empty value."""
    return bool(os.environ.get(_ENV_VAR, "").strip())


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # Durability tuned for "flush every 5s" — no need for SYNCHRONOUS=FULL.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# --- key encoding --------------------------------------------------------

def _encode_key(bucket: str, key: Any) -> str:
    if bucket in _TUPLE_KEY_BUCKETS and isinstance(key, tuple):
        # All current tuple keys are 2-tuples of strings.
        return ":".join(str(p) for p in key)
    return str(key)


def _decode_key(bucket: str, key: str) -> Any:
    if bucket in _TUPLE_KEY_BUCKETS:
        parts = key.split(":", 1)
        if len(parts) == 2:
            return (parts[0], parts[1])
    return key


# --- (de)serialization --------------------------------------------------

def _serialize(v: Any) -> Any:
    if is_dataclass(v) and not isinstance(v, type):
        return asdict(v)
    if isinstance(v, dict):
        return {str(k): _serialize(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_serialize(x) for x in v]
    return v


def _deserialize(bucket: str, raw: Any) -> Any:
    cls = _DATACLASS_BUCKETS.get(bucket)
    if cls is not None and isinstance(raw, dict):
        # Drop fields the dataclass does not know about (schema drift safety).
        fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in fields})
    return raw


# --- schema --------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_store (
    bucket TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (bucket, key)
)
"""


def init_db(path: str) -> None:
    """Create the schema if missing."""
    conn = _connect(path)
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# --- load / save ---------------------------------------------------------

def load_world(world: World, path: str) -> None:
    """Replace world contents from DB. No-op if DB is empty / missing."""
    if not Path(path).exists():
        return
    init_db(path)
    conn = _connect(path)
    try:
        cur = conn.execute("SELECT bucket, key, value FROM kv_store")
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return

    # Clear only the buckets we're about to restore, so partial snapshots
    # don't leave stale entries from the default world.
    touched: set[str] = set()
    grouped: dict[str, list[tuple[str, str]]] = {}
    for bucket, key, value in rows:
        grouped.setdefault(bucket, []).append((key, value))
        touched.add(bucket)

    for bucket in touched:
        target = getattr(world, bucket, None)
        if isinstance(target, dict):
            target.clear()

    for bucket, entries in grouped.items():
        target = getattr(world, bucket, None)
        if not isinstance(target, dict):
            log.warning("persistence: unknown bucket %r in DB, skipping", bucket)
            continue
        for key, value in entries:
            try:
                decoded_value = json.loads(value)
            except json.JSONDecodeError:
                log.warning("persistence: bad JSON in %s[%s], skipping", bucket, key)
                continue
            decoded_key = _decode_key(bucket, key)
            target[decoded_key] = _deserialize(bucket, decoded_value)


def save_world(world: World, path: str) -> None:
    """Write the entire world snapshot to DB (wipe-and-rewrite)."""
    init_db(path)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM kv_store")
        for bucket in _BUCKETS:
            target = getattr(world, bucket, None)
            if not isinstance(target, dict):
                continue
            for key, value in target.items():
                conn.execute(
                    "INSERT INTO kv_store(bucket, key, value) VALUES (?, ?, ?)",
                    (bucket, _encode_key(bucket, key), json.dumps(_serialize(value))),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wipe_db(path: str) -> None:
    """Delete all rows. Called from /admin/reset."""
    if not Path(path).exists():
        return
    init_db(path)
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM kv_store")
        conn.commit()
    finally:
        conn.close()


# --- autosave task -------------------------------------------------------

async def start_autosave(world: World, path: str, interval_sec: float = 5.0) -> asyncio.Task:
    """Periodically flush the world to disk. Returns the task so the caller
    can cancel it on shutdown."""

    async def _loop() -> None:
        while True:
            try:
                await asyncio.sleep(interval_sec)
                # sqlite3 is blocking — keep the event loop responsive.
                await asyncio.to_thread(save_world, world, path)
            except asyncio.CancelledError:
                # Final flush on shutdown so we don't lose the last delta.
                try:
                    await asyncio.to_thread(save_world, world, path)
                except Exception:
                    log.exception("persistence: final flush failed")
                raise
            except Exception:
                # A DB hiccup should not kill the loop — next tick retries.
                log.exception("persistence: autosave tick failed")

    return asyncio.create_task(_loop(), name="dapi-persistence-autosave")

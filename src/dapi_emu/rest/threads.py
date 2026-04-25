"""Thread endpoints: creation, membership, archived listings."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import audit
from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


# --- helpers --------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_channel(channel_id: str) -> Any:
    ch = WORLD.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return ch


def _require_thread(thread_id: str) -> dict:
    th = WORLD.threads.get(thread_id)
    if not th:
        raise HTTPException(status_code=404, detail={"code": 10003, "message": "Unknown Channel"})
    return th


def _require_message(channel_id: str, message_id: str) -> Any:
    m = WORLD.messages.get(message_id)
    if not m or m.channel_id != channel_id:
        raise HTTPException(status_code=404, detail={"code": 10008, "message": "Unknown Message"})
    return m


def _build_thread(
    *,
    name: str,
    thread_type: int,
    parent_id: str,
    guild_id: str | None,
    owner_id: str,
    auto_archive_duration: int,
    invitable: bool,
    rate_limit_per_user: int = 0,
) -> dict:
    tid = new_snowflake()
    created = _now_iso()
    thread: dict[str, Any] = {
        "id": tid,
        "type": thread_type,
        "guild_id": guild_id,
        "parent_id": parent_id,
        "owner_id": owner_id,
        "name": name,
        "last_message_id": None,
        "message_count": 0,
        "member_count": 1,
        "rate_limit_per_user": rate_limit_per_user,
        "flags": 0,
        "thread_metadata": {
            "archived": False,
            "auto_archive_duration": auto_archive_duration,
            "archive_timestamp": created,
            "locked": False,
            "invitable": invitable,
            "create_timestamp": created,
        },
    }
    WORLD.threads[tid] = thread
    # channel-like storage so messages can be posted into the thread
    WORLD.channel_messages.setdefault(tid, [])
    # owner is auto-added
    _set_thread_member(tid, owner_id)
    return thread


def _set_thread_member(thread_id: str, user_id: str) -> dict:
    member = {
        "id": thread_id,
        "user_id": user_id,
        "join_timestamp": _now_iso(),
        "flags": 0,
    }
    WORLD.thread_members[(thread_id, user_id)] = member
    th = WORLD.threads.get(thread_id)
    if th is not None:
        th["member_count"] = sum(1 for (tid, _uid) in WORLD.thread_members if tid == thread_id)
    return member


def _drop_thread_member(thread_id: str, user_id: str) -> None:
    WORLD.thread_members.pop((thread_id, user_id), None)
    th = WORLD.threads.get(thread_id)
    if th is not None:
        th["member_count"] = sum(1 for (tid, _uid) in WORLD.thread_members if tid == thread_id)


def _publish_members_update(thread_id: str) -> None:
    th = WORLD.threads.get(thread_id)
    if not th:
        return
    added = [m for (tid, _u), m in WORLD.thread_members.items() if tid == thread_id]
    WORLD.bus.publish("THREAD_MEMBERS_UPDATE", {
        "id": thread_id,
        "guild_id": th.get("guild_id"),
        "member_count": th.get("member_count", len(added)),
        "added_members": added,
        "removed_member_ids": [],
    })


# --- Create ---------------------------------------------------------------

@router.post("/channels/{channel_id}/messages/{message_id}/threads", status_code=201)
async def start_thread_from_message(
    channel_id: str,
    message_id: str,
    body: dict,
    bot: User = Depends(require_bot),
) -> dict:
    ch = _require_channel(channel_id)
    _require_message(channel_id, message_id)
    thread = _build_thread(
        name=body.get("name", "thread"),
        thread_type=11,  # PUBLIC_THREAD
        parent_id=channel_id,
        guild_id=ch.guild_id,
        owner_id=bot.id,
        auto_archive_duration=int(body.get("auto_archive_duration", 1440)),
        invitable=bool(body.get("invitable", True)),
        rate_limit_per_user=int(body.get("rate_limit_per_user", 0) or 0),
    )
    # thread id == starter message id per Discord convention (but we keep it separate)
    WORLD.bus.publish("THREAD_CREATE", thread)
    audit.log(  # THREAD_CREATE
        ch.guild_id, bot.id, thread["id"], 120,
        changes=[
            {"key": "name", "new_value": thread["name"]},
            {"key": "type", "new_value": thread["type"]},
        ],
    )
    from .. import system_messages
    system_messages.thread_created(channel_id, bot.id, thread["name"], thread["id"])
    system_messages.thread_starter(thread["id"], message_id, channel_id)
    return thread


@router.post("/channels/{channel_id}/threads", status_code=201)
async def start_thread(
    channel_id: str,
    body: dict,
    bot: User = Depends(require_bot),
) -> dict:
    ch = _require_channel(channel_id)
    thread_type = int(body.get("type", 11))
    thread = _build_thread(
        name=body.get("name", "thread"),
        thread_type=thread_type,
        parent_id=channel_id,
        guild_id=ch.guild_id,
        owner_id=bot.id,
        auto_archive_duration=int(body.get("auto_archive_duration", 1440)),
        invitable=bool(body.get("invitable", True)),
        rate_limit_per_user=int(body.get("rate_limit_per_user", 0) or 0),
    )
    # Forum post: body.message creates the initial message
    if "message" in body and isinstance(body["message"], dict):
        msg_body = body["message"]
        msg = WORLD.create_message(
            channel_id=thread["id"],
            author_id=bot.id,
            content=msg_body.get("content", "") or "",
            embeds=msg_body.get("embeds") or [],
            components=msg_body.get("components") or [],
            flags=int(msg_body.get("flags", 0) or 0),
        )
        thread["last_message_id"] = msg.id
        thread["message_count"] = 1
        WORLD.bus.publish("MESSAGE_CREATE", msg.to_dict(WORLD.users))
    WORLD.bus.publish("THREAD_CREATE", thread)
    audit.log(  # THREAD_CREATE
        ch.guild_id, bot.id, thread["id"], 120,
        changes=[
            {"key": "name", "new_value": thread["name"]},
            {"key": "type", "new_value": thread["type"]},
        ],
    )
    from .. import system_messages
    system_messages.thread_created(channel_id, bot.id, thread["name"], thread["id"])
    return thread


# --- Membership -----------------------------------------------------------

@router.put("/channels/{channel_id}/thread-members/@me", status_code=204)
async def join_thread(channel_id: str, bot: User = Depends(require_bot)) -> None:
    _require_thread(channel_id)
    _set_thread_member(channel_id, bot.id)
    _publish_members_update(channel_id)


@router.put("/channels/{channel_id}/thread-members/{user_id}", status_code=204)
async def add_thread_member(
    channel_id: str, user_id: str, _bot: User = Depends(require_bot)
) -> None:
    _require_thread(channel_id)
    if user_id not in WORLD.users:
        raise HTTPException(status_code=404, detail={"code": 10013, "message": "Unknown User"})
    _set_thread_member(channel_id, user_id)
    _publish_members_update(channel_id)


@router.delete("/channels/{channel_id}/thread-members/@me", status_code=204)
async def leave_thread(channel_id: str, bot: User = Depends(require_bot)) -> None:
    _require_thread(channel_id)
    _drop_thread_member(channel_id, bot.id)
    th = WORLD.threads.get(channel_id)
    WORLD.bus.publish("THREAD_MEMBERS_UPDATE", {
        "id": channel_id,
        "guild_id": th.get("guild_id") if th else None,
        "member_count": th.get("member_count") if th else 0,
        "added_members": [],
        "removed_member_ids": [bot.id],
    })


@router.delete("/channels/{channel_id}/thread-members/{user_id}", status_code=204)
async def remove_thread_member(
    channel_id: str, user_id: str, _bot: User = Depends(require_bot)
) -> None:
    _require_thread(channel_id)
    _drop_thread_member(channel_id, user_id)
    th = WORLD.threads.get(channel_id)
    WORLD.bus.publish("THREAD_MEMBERS_UPDATE", {
        "id": channel_id,
        "guild_id": th.get("guild_id") if th else None,
        "member_count": th.get("member_count") if th else 0,
        "added_members": [],
        "removed_member_ids": [user_id],
    })


@router.get("/channels/{channel_id}/thread-members/{user_id}")
async def get_thread_member(
    channel_id: str, user_id: str, _bot: User = Depends(require_bot)
) -> dict:
    _require_thread(channel_id)
    member = WORLD.thread_members.get((channel_id, user_id))
    if not member:
        raise HTTPException(status_code=404, detail={"code": 10007, "message": "Unknown Member"})
    return member


@router.get("/channels/{channel_id}/thread-members")
async def list_thread_members(
    channel_id: str, _bot: User = Depends(require_bot)
) -> list[dict]:
    _require_thread(channel_id)
    return [m for (tid, _uid), m in WORLD.thread_members.items() if tid == channel_id]


# --- Archived listings ----------------------------------------------------

def _list_archived(parent_id: str, *, private: bool, owner_id: str | None = None,
                   before: str | None, limit: int) -> dict:
    threads: list[dict] = []
    for t in WORLD.threads.values():
        if t.get("parent_id") != parent_id:
            continue
        is_private = t.get("type") == 12
        if private != is_private:
            continue
        if owner_id is not None and t.get("owner_id") != owner_id:
            continue
        meta = t.get("thread_metadata") or {}
        if not meta.get("archived"):
            continue
        if before:
            try:
                if meta.get("archive_timestamp", "") >= before:
                    continue
            except TypeError:
                pass
        threads.append(t)
    threads.sort(key=lambda t: t.get("thread_metadata", {}).get("archive_timestamp", ""),
                 reverse=True)
    threads = threads[:limit]
    members = [
        m for (tid, _uid), m in WORLD.thread_members.items()
        if any(t["id"] == tid for t in threads)
    ]
    return {"threads": threads, "members": members, "has_more": False}


@router.get("/channels/{channel_id}/threads/archived/public")
async def list_public_archived(
    channel_id: str,
    before: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    _bot: User = Depends(require_bot),
) -> dict:
    _require_channel(channel_id)
    return _list_archived(channel_id, private=False, before=before, limit=limit)


@router.get("/channels/{channel_id}/threads/archived/private")
async def list_private_archived(
    channel_id: str,
    before: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    _bot: User = Depends(require_bot),
) -> dict:
    _require_channel(channel_id)
    return _list_archived(channel_id, private=True, before=before, limit=limit)


@router.get("/channels/{channel_id}/users/@me/threads/archived/private")
async def list_my_private_archived(
    channel_id: str,
    before: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    bot: User = Depends(require_bot),
) -> dict:
    _require_channel(channel_id)
    return _list_archived(channel_id, private=True, owner_id=bot.id, before=before, limit=limit)


@router.get("/guilds/{guild_id}/threads/active")
async def list_active_guild_threads(
    guild_id: str, _bot: User = Depends(require_bot)
) -> dict:
    if guild_id not in WORLD.guilds:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    threads = [
        t for t in WORLD.threads.values()
        if t.get("guild_id") == guild_id and not (t.get("thread_metadata") or {}).get("archived")
    ]
    members = [
        m for (tid, _uid), m in WORLD.thread_members.items()
        if any(t["id"] == tid for t in threads)
    ]
    return {"threads": threads, "members": members}

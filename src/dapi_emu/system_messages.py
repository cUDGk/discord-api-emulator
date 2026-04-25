"""System message helpers — auto-post messages on member joins, pins, boosts, etc.

Discord posts a stream of typed messages alongside user content: "X joined!",
"X pinned a message", "X boosted the server", thread-starter banners, stage
notifications, and so on. Each has a numeric `type` on the Message object.

This module provides one entry point per event so routes can drop in a single
line and stay readable.
"""
from __future__ import annotations

from typing import Any, Optional

from .state import WORLD, Message, User


# --- MessageType constants (subset Discord defines) ----------------------
DEFAULT = 0
RECIPIENT_ADD = 1
RECIPIENT_REMOVE = 2
CALL = 3
CHANNEL_NAME_CHANGE = 4
CHANNEL_ICON_CHANGE = 5
CHANNEL_PINNED_MESSAGE = 6
USER_JOIN = 7
GUILD_BOOST = 8
GUILD_BOOST_TIER_1 = 9
GUILD_BOOST_TIER_2 = 10
GUILD_BOOST_TIER_3 = 11
CHANNEL_FOLLOW_ADD = 12
GUILD_DISCOVERY_DISQUALIFIED = 14
GUILD_DISCOVERY_REQUALIFIED = 15
THREAD_CREATED = 18
REPLY = 19
CHAT_INPUT_COMMAND = 20
THREAD_STARTER_MESSAGE = 21
GUILD_INVITE_REMINDER = 22
CONTEXT_MENU_COMMAND = 23
AUTO_MODERATION_ACTION = 24
ROLE_SUBSCRIPTION_PURCHASE = 25
STAGE_START = 27
STAGE_END = 28
STAGE_SPEAKER = 29
STAGE_TOPIC = 31


# Discord-style invariant system user id. Lazily seeded in WORLD.users.
SYSTEM_USER_ID = "643945264868503973"


def _ensure_system_user() -> User:
    u = WORLD.users.get(SYSTEM_USER_ID)
    if u is None:
        u = User(
            id=SYSTEM_USER_ID, username="System", discriminator="0",
            global_name="System", system=True, bot=False,
        )
        WORLD.users[SYSTEM_USER_ID] = u
    return u


def _system_channel_for(guild_id: Optional[str]) -> Optional[str]:
    """Resolve which channel system messages should be posted into.

    Order: guild.system_channel_id -> first text channel in the guild.
    """
    if not guild_id or guild_id not in WORLD.guilds:
        return None
    g = WORLD.guilds[guild_id]
    if g.system_channel_id and g.system_channel_id in WORLD.channels:
        return g.system_channel_id
    for cid in g.channel_ids:
        ch = WORLD.channels.get(cid)
        if ch is not None and ch.type == 0:
            return cid
    return None


def post(
    *,
    channel_id: Optional[str],
    msg_type: int,
    author_id: Optional[str] = None,
    content: str = "",
    **extra: Any,
) -> Optional[Message]:
    """Create + dispatch a system message. No-op if channel doesn't exist."""
    if not channel_id or channel_id not in WORLD.channels:
        return None
    if author_id is None or author_id not in WORLD.users:
        author_id = _ensure_system_user().id
    msg = WORLD.create_message(
        channel_id=channel_id,
        author_id=author_id,
        content=content,
        type=msg_type,
        **extra,
    )
    payload = msg.to_dict(WORLD.users)
    WORLD.bus.publish("MESSAGE_CREATE", payload)
    return msg


# --- High-level event helpers --------------------------------------------

def member_joined(guild_id: str, user_id: str) -> Optional[Message]:
    """Post 'X joined!' to the guild's system channel."""
    return post(
        channel_id=_system_channel_for(guild_id),
        msg_type=USER_JOIN,
        author_id=user_id,
    )


def message_pinned(channel_id: str, pinner_user_id: str,
                   target_message_id: str) -> Optional[Message]:
    return post(
        channel_id=channel_id,
        msg_type=CHANNEL_PINNED_MESSAGE,
        author_id=pinner_user_id,
        referenced_message={"message_id": target_message_id, "channel_id": channel_id},
    )


def thread_created(parent_channel_id: str, creator_user_id: str,
                   thread_name: str, thread_id: str) -> Optional[Message]:
    return post(
        channel_id=parent_channel_id,
        msg_type=THREAD_CREATED,
        author_id=creator_user_id,
        content=thread_name,
        referenced_message={"channel_id": thread_id},
    )


def thread_starter(thread_id: str, original_message_id: str,
                   original_channel_id: str) -> Optional[Message]:
    """Posted as the first message inside a thread that was created from
    an existing message. Type 21."""
    return post(
        channel_id=thread_id,
        msg_type=THREAD_STARTER_MESSAGE,
        referenced_message={"message_id": original_message_id,
                            "channel_id": original_channel_id},
    )


def channel_name_changed(channel_id: str, actor_user_id: str,
                         new_name: str) -> Optional[Message]:
    return post(
        channel_id=channel_id, msg_type=CHANNEL_NAME_CHANGE,
        author_id=actor_user_id, content=new_name,
    )


def channel_icon_changed(channel_id: str, actor_user_id: str) -> Optional[Message]:
    return post(channel_id=channel_id, msg_type=CHANNEL_ICON_CHANGE,
                author_id=actor_user_id)


def recipient_added(channel_id: str, actor_user_id: str,
                    added_user_id: str) -> Optional[Message]:
    """Group DM: someone was added."""
    extra: dict[str, Any] = {}
    added = WORLD.users.get(added_user_id)
    if added is not None:
        extra["mentions"] = [added_user_id]
    return post(channel_id=channel_id, msg_type=RECIPIENT_ADD,
                author_id=actor_user_id, **extra)


def recipient_removed(channel_id: str, actor_user_id: str,
                      removed_user_id: str) -> Optional[Message]:
    extra: dict[str, Any] = {}
    removed = WORLD.users.get(removed_user_id)
    if removed is not None:
        extra["mentions"] = [removed_user_id]
    return post(channel_id=channel_id, msg_type=RECIPIENT_REMOVE,
                author_id=actor_user_id, **extra)


def call_started(channel_id: str, caller_user_id: str) -> Optional[Message]:
    """DM/group DM: voice call started."""
    return post(channel_id=channel_id, msg_type=CALL, author_id=caller_user_id)


def guild_boosted(guild_id: str, booster_user_id: str,
                  *, tier: int = 0) -> Optional[Message]:
    """Post a boost notification. tier=0..3.

    tier=0 is the bare 'X boosted the server' message; 1/2/3 are tier-up
    notifications (different MessageType per Discord).
    """
    type_map = {0: GUILD_BOOST, 1: GUILD_BOOST_TIER_1,
                2: GUILD_BOOST_TIER_2, 3: GUILD_BOOST_TIER_3}
    return post(
        channel_id=_system_channel_for(guild_id),
        msg_type=type_map.get(tier, GUILD_BOOST),
        author_id=booster_user_id,
    )


def channel_follow_added(channel_id: str, follower_user_id: str,
                         source_guild_name: str) -> Optional[Message]:
    """Announcement-channel follow notification."""
    return post(channel_id=channel_id, msg_type=CHANNEL_FOLLOW_ADD,
                author_id=follower_user_id, content=source_guild_name)


def stage_started(channel_id: str, host_user_id: str,
                  topic: str = "") -> Optional[Message]:
    return post(channel_id=channel_id, msg_type=STAGE_START,
                author_id=host_user_id, content=topic)


def stage_ended(channel_id: str, host_user_id: str) -> Optional[Message]:
    return post(channel_id=channel_id, msg_type=STAGE_END,
                author_id=host_user_id)


def stage_speaker(channel_id: str, speaker_user_id: str) -> Optional[Message]:
    return post(channel_id=channel_id, msg_type=STAGE_SPEAKER,
                author_id=speaker_user_id)


def stage_topic(channel_id: str, host_user_id: str,
                topic: str) -> Optional[Message]:
    return post(channel_id=channel_id, msg_type=STAGE_TOPIC,
                author_id=host_user_id, content=topic)


def auto_moderation_action(channel_id: str, content: str = "") -> Optional[Message]:
    """AutoMod posted a notice in a channel."""
    return post(channel_id=channel_id, msg_type=AUTO_MODERATION_ACTION,
                content=content)


def guild_invite_reminder(channel_id: str) -> Optional[Message]:
    return post(channel_id=channel_id, msg_type=GUILD_INVITE_REMINDER)


def role_subscription_purchase(channel_id: str, buyer_user_id: str,
                               role_name: str) -> Optional[Message]:
    return post(channel_id=channel_id, msg_type=ROLE_SUBSCRIPTION_PURCHASE,
                author_id=buyer_user_id, content=role_name)

"""In-memory world state. All mutation goes through this module so the gateway
can observe changes via the event bus."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from . import snowflake


# --- Domain objects -------------------------------------------------------

@dataclass
class User:
    id: str
    username: str
    discriminator: str = "0"
    global_name: str | None = None
    avatar: str | None = None
    bot: bool = False
    system: bool = False
    email: str | None = None
    verified: bool = True
    flags: int = 0
    public_flags: int = 0

    def to_dict(self, *, private: bool = False) -> dict[str, Any]:
        d = {
            "id": self.id,
            "username": self.username,
            "discriminator": self.discriminator,
            "global_name": self.global_name,
            "avatar": self.avatar,
            "bot": self.bot,
            "system": self.system,
            "flags": self.flags,
            "public_flags": self.public_flags,
        }
        if private:
            d["email"] = self.email
            d["verified"] = self.verified
            d["mfa_enabled"] = False
            d["locale"] = "en-US"
        return d


@dataclass
class Role:
    id: str
    guild_id: str
    name: str
    color: int = 0
    hoist: bool = False
    position: int = 0
    permissions: str = "0"
    managed: bool = False
    mentionable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "hoist": self.hoist,
            "position": self.position,
            "permissions": self.permissions,
            "managed": self.managed,
            "mentionable": self.mentionable,
            "flags": 0,
        }


@dataclass
class Member:
    user_id: str
    guild_id: str
    nick: str | None = None
    roles: list[str] = field(default_factory=list)
    joined_at: str = ""
    deaf: bool = False
    mute: bool = False
    flags: int = 0

    def to_dict(self, users: dict[str, User]) -> dict[str, Any]:
        return {
            "user": users[self.user_id].to_dict(),
            "nick": self.nick,
            "avatar": None,
            "roles": self.roles,
            "joined_at": self.joined_at,
            "premium_since": None,
            "deaf": self.deaf,
            "mute": self.mute,
            "flags": self.flags,
            "pending": False,
            "communication_disabled_until": None,
        }


@dataclass
class Channel:
    id: str
    type: int  # 0 GUILD_TEXT, 1 DM, 2 GUILD_VOICE, 4 GUILD_CATEGORY, ...
    guild_id: str | None = None
    name: str | None = None
    position: int = 0
    topic: str | None = None
    nsfw: bool = False
    parent_id: str | None = None
    rate_limit_per_user: int = 0
    recipients: list[str] = field(default_factory=list)
    last_message_id: str | None = None

    def to_dict(self, users: dict[str, User] | None = None) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "flags": 0,
            "last_message_id": self.last_message_id,
        }
        if self.guild_id is not None:
            d.update({
                "guild_id": self.guild_id,
                "name": self.name,
                "position": self.position,
                "topic": self.topic,
                "nsfw": self.nsfw,
                "parent_id": self.parent_id,
                "rate_limit_per_user": self.rate_limit_per_user,
                "permission_overwrites": [],
            })
        else:
            d["recipients"] = [users[uid].to_dict() for uid in self.recipients] if users else []
        return d


@dataclass
class Message:
    id: str
    channel_id: str
    author_id: str
    content: str = ""
    timestamp: str = ""
    edited_timestamp: str | None = None
    tts: bool = False
    mention_everyone: bool = False
    mentions: list[str] = field(default_factory=list)
    mention_roles: list[str] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    embeds: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[dict[str, Any]] = field(default_factory=list)
    pinned: bool = False
    type: int = 0
    flags: int = 0
    referenced_message: dict[str, Any] | None = None
    guild_id: str | None = None

    def to_dict(self, users: dict[str, User]) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "author": users[self.author_id].to_dict(),
            "content": self.content,
            "timestamp": self.timestamp,
            "edited_timestamp": self.edited_timestamp,
            "tts": self.tts,
            "mention_everyone": self.mention_everyone,
            "mentions": [users[uid].to_dict() for uid in self.mentions if uid in users],
            "mention_roles": self.mention_roles,
            "attachments": self.attachments,
            "embeds": self.embeds,
            "components": self.components,
            "reactions": self.reactions,
            "pinned": self.pinned,
            "type": self.type,
            "flags": self.flags,
            "referenced_message": self.referenced_message,
        }


@dataclass
class Guild:
    id: str
    name: str
    owner_id: str
    icon: str | None = None
    description: str | None = None
    channel_ids: list[str] = field(default_factory=list)
    role_ids: list[str] = field(default_factory=list)
    member_ids: list[str] = field(default_factory=list)
    emojis: list[dict[str, Any]] = field(default_factory=list)
    features: list[str] = field(default_factory=list)

    def to_dict(self, world: World, *, full: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "description": self.description,
            "splash": None,
            "discovery_splash": None,
            "owner_id": self.owner_id,
            "region": None,
            "afk_channel_id": None,
            "afk_timeout": 300,
            "verification_level": 0,
            "default_message_notifications": 0,
            "explicit_content_filter": 0,
            "roles": [world.roles[rid].to_dict() for rid in self.role_ids if rid in world.roles],
            "emojis": self.emojis,
            "features": self.features,
            "mfa_level": 0,
            "application_id": None,
            "system_channel_id": None,
            "system_channel_flags": 0,
            "rules_channel_id": None,
            "max_presences": None,
            "max_members": 500000,
            "vanity_url_code": None,
            "banner": None,
            "premium_tier": 0,
            "premium_subscription_count": 0,
            "preferred_locale": "en-US",
            "public_updates_channel_id": None,
            "nsfw_level": 0,
            "premium_progress_bar_enabled": False,
            "safety_alerts_channel_id": None,
        }
        if full:
            d.update({
                "joined_at": "2024-01-01T00:00:00+00:00",
                "large": False,
                "unavailable": False,
                "member_count": len(self.member_ids),
                "voice_states": [],
                "members": [
                    world.members[(uid, self.id)].to_dict(world.users)
                    for uid in self.member_ids
                    if (uid, self.id) in world.members
                ],
                "channels": [
                    world.channels[cid].to_dict()
                    for cid in self.channel_ids
                    if cid in world.channels
                ],
                "threads": [],
                "presences": [],
                "stage_instances": [],
                "guild_scheduled_events": [],
            })
        return d


@dataclass
class Application:
    id: str
    name: str
    owner_id: str
    bot_id: str
    description: str = ""
    icon: str | None = None
    flags: int = 0
    public_key: str = ""

    def to_dict(self, users: dict[str, User]) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "description": self.description,
            "bot_public": True,
            "bot_require_code_grant": False,
            "verify_key": self.public_key,
            "owner": users[self.owner_id].to_dict() if self.owner_id in users else None,
            "flags": self.flags,
            "hook": True,
            "tags": [],
            "install_params": {"scopes": ["bot", "applications.commands"], "permissions": "0"},
        }


# --- World ----------------------------------------------------------------

class World:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.guilds: dict[str, Guild] = {}
        self.channels: dict[str, Channel] = {}
        self.messages: dict[str, Message] = {}  # id -> message
        self.channel_messages: dict[str, list[str]] = {}  # channel_id -> ordered [msg_id]
        self.roles: dict[str, Role] = {}
        self.members: dict[tuple[str, str], Member] = {}  # (user_id, guild_id)
        self.applications: dict[str, Application] = {}
        self.bot_tokens: dict[str, str] = {}  # token -> bot_user_id
        self.commands: dict[str, dict[str, Any]] = {}  # command_id -> payload
        self.guild_commands: dict[tuple[str, str], list[str]] = {}  # (app_id, guild_id) -> [cmd_id]
        self.global_commands: dict[str, list[str]] = {}  # app_id -> [cmd_id]

        # Phase 3+ storage — expanded by sub-routers.
        self.threads: dict[str, dict[str, Any]] = {}  # thread_id -> thread object
        self.thread_members: dict[tuple[str, str], dict[str, Any]] = {}  # (thread_id, user_id)
        self.webhooks: dict[str, dict[str, Any]] = {}
        self.invites: dict[str, dict[str, Any]] = {}  # code -> invite
        self.guild_invites: dict[str, list[str]] = {}  # guild_id -> [code]
        self.emojis: dict[str, dict[str, Any]] = {}
        self.guild_emojis: dict[str, list[str]] = {}
        self.stickers: dict[str, dict[str, Any]] = {}
        self.guild_stickers: dict[str, list[str]] = {}
        self.sticker_packs: dict[str, dict[str, Any]] = {}
        self.bans: dict[tuple[str, str], dict[str, Any]] = {}  # (guild_id, user_id)
        self.audit_logs: dict[str, list[dict[str, Any]]] = {}  # guild_id -> entries
        self.voice_states: dict[tuple[str, str], dict[str, Any]] = {}  # (guild_id, user_id)
        self.stage_instances: dict[str, dict[str, Any]] = {}
        self.scheduled_events: dict[str, dict[str, Any]] = {}
        self.guild_scheduled_events: dict[str, list[str]] = {}
        self.automod_rules: dict[str, dict[str, Any]] = {}
        self.guild_automod_rules: dict[str, list[str]] = {}
        self.polls: dict[str, dict[str, Any]] = {}  # message_id -> poll state
        self.entitlements: dict[str, dict[str, Any]] = {}
        self.skus: dict[str, dict[str, Any]] = {}
        self.teams: dict[str, dict[str, Any]] = {}
        self.oauth_authorizations: dict[str, dict[str, Any]] = {}  # code -> grant
        self.oauth_tokens: dict[str, dict[str, Any]] = {}  # access_token -> grant
        self.pinned_messages: dict[str, list[str]] = {}  # channel_id -> [msg_id]
        self.interaction_tokens: dict[str, dict[str, Any]] = {}  # token -> {interaction_id, app_id, channel_id, user_id, original_msg_id?}
        self.channel_overwrites: dict[str, list[dict[str, Any]]] = {}  # channel_id -> overwrites

        self.bus = EventBus()

    # User helpers
    def create_user(self, username: str, *, bot: bool = False, discriminator: str = "0") -> User:
        uid = snowflake.generate()
        u = User(id=uid, username=username, discriminator=discriminator, bot=bot, global_name=username)
        self.users[uid] = u
        return u

    def register_bot(self, user: User, token: str | None = None) -> tuple[Application, str]:
        app_id = snowflake.generate()
        token = token or f"bot.{user.id}.{snowflake.generate()}"
        app = Application(id=app_id, name=user.username, owner_id=user.id, bot_id=user.id)
        self.applications[app_id] = app
        self.bot_tokens[token] = user.id
        return app, token

    # Role helpers
    def create_role(self, guild_id: str, name: str, **kwargs: Any) -> Role:
        rid = snowflake.generate()
        r = Role(id=rid, guild_id=guild_id, name=name, **kwargs)
        self.roles[rid] = r
        if guild_id in self.guilds:
            self.guilds[guild_id].role_ids.append(rid)
        return r

    # Member helpers
    def add_member(self, user_id: str, guild_id: str, *, nick: str | None = None,
                   roles: list[str] | None = None, joined_at: str = "2024-01-01T00:00:00+00:00") -> Member:
        m = Member(
            user_id=user_id, guild_id=guild_id, nick=nick,
            roles=roles or [], joined_at=joined_at,
        )
        self.members[(user_id, guild_id)] = m
        g = self.guilds.get(guild_id)
        if g and user_id not in g.member_ids:
            g.member_ids.append(user_id)
        return m

    # Channel helpers
    def create_channel(self, name: str, *, type: int = 0, guild_id: str | None = None,
                       parent_id: str | None = None) -> Channel:
        cid = snowflake.generate()
        ch = Channel(id=cid, type=type, name=name, guild_id=guild_id, parent_id=parent_id)
        self.channels[cid] = ch
        self.channel_messages[cid] = []
        if guild_id and guild_id in self.guilds:
            self.guilds[guild_id].channel_ids.append(cid)
        return ch

    # Guild helpers
    def create_guild(self, name: str, owner_id: str) -> Guild:
        gid = snowflake.generate()
        g = Guild(id=gid, name=name, owner_id=owner_id)
        self.guilds[gid] = g
        # Create @everyone role
        everyone = Role(id=gid, guild_id=gid, name="@everyone", permissions="104324673")
        self.roles[gid] = everyone
        g.role_ids.append(gid)
        # Add owner as member
        self.add_member(owner_id, gid)
        return g

    # Message helpers
    def create_message(self, channel_id: str, author_id: str, content: str,
                       **kwargs: Any) -> Message:
        from datetime import datetime, timezone
        mid = snowflake.generate()
        ch = self.channels.get(channel_id)
        msg = Message(
            id=mid,
            channel_id=channel_id,
            author_id=author_id,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            guild_id=ch.guild_id if ch else None,
            **kwargs,
        )
        self.messages[mid] = msg
        self.channel_messages.setdefault(channel_id, []).append(mid)
        if ch:
            ch.last_message_id = mid
        return msg


# --- Event Bus (gateway dispatch) -----------------------------------------

class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[tuple[str, dict[str, Any]]]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[tuple[str, dict[str, Any]]]:
        q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[tuple[str, dict[str, Any]]]) -> None:
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event_name: str, data: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            q.put_nowait((event_name, data))


# --- Singleton ------------------------------------------------------------

WORLD = World()

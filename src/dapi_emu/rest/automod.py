from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import audit
from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


def _require_guild(guild_id: str):
    g = WORLD.guilds.get(guild_id)
    if not g:
        raise HTTPException(status_code=404, detail={"code": 10004, "message": "Unknown Guild"})
    return g


def _require_rule(guild_id: str, rule_id: str) -> dict[str, Any]:
    rule = WORLD.automod_rules.get(rule_id)
    if not rule or rule.get("guild_id") != guild_id:
        raise HTTPException(status_code=404, detail={"code": 10068, "message": "Unknown Auto Moderation Rule"})
    return rule


@router.get("/guilds/{guild_id}/auto-moderation/rules")
async def list_automod_rules(guild_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    _require_guild(guild_id)
    ids = WORLD.guild_automod_rules.get(guild_id, [])
    return [WORLD.automod_rules[i] for i in ids if i in WORLD.automod_rules]


@router.get("/guilds/{guild_id}/auto-moderation/rules/{rule_id}")
async def get_automod_rule(guild_id: str, rule_id: str, _bot: User = Depends(require_bot)) -> dict:
    return _require_rule(guild_id, rule_id)


@router.post("/guilds/{guild_id}/auto-moderation/rules", status_code=200)
async def create_automod_rule(guild_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    _require_guild(guild_id)
    name = body.get("name")
    event_type = body.get("event_type")
    trigger_type = body.get("trigger_type")
    actions = body.get("actions")
    if not name or event_type is None or trigger_type is None or actions is None:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "name, event_type, trigger_type, actions are required"})
    rule_id = new_snowflake()
    rule: dict[str, Any] = {
        "id": rule_id,
        "guild_id": guild_id,
        "name": name,
        "creator_id": bot.id,
        "event_type": int(event_type),
        "trigger_type": int(trigger_type),
        "trigger_metadata": body.get("trigger_metadata", {}),
        "actions": actions,
        "enabled": bool(body.get("enabled", False)),
        "exempt_roles": list(body.get("exempt_roles", [])),
        "exempt_channels": list(body.get("exempt_channels", [])),
    }
    WORLD.automod_rules[rule_id] = rule
    WORLD.guild_automod_rules.setdefault(guild_id, []).append(rule_id)
    WORLD.bus.publish("AUTO_MODERATION_RULE_CREATE", rule)
    audit.log(  # AUTO_MODERATION_RULE_CREATE
        guild_id, bot.id, rule_id, 140,
        changes=[{"key": "name", "new_value": name}],
    )
    return rule


@router.patch("/guilds/{guild_id}/auto-moderation/rules/{rule_id}")
async def edit_automod_rule(guild_id: str, rule_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    rule = _require_rule(guild_id, rule_id)
    changes: list[dict] = []
    for field in (
        "name",
        "event_type",
        "trigger_metadata",
        "actions",
        "enabled",
        "exempt_roles",
        "exempt_channels",
    ):
        if field in body:
            old = rule.get(field)
            new = body[field]
            if old != new:
                changes.append({"key": field, "old_value": old, "new_value": new})
            rule[field] = new
    WORLD.bus.publish("AUTO_MODERATION_RULE_UPDATE", rule)
    audit.log(guild_id, bot.id, rule_id, 141, changes=changes)  # AUTO_MOD_RULE_UPDATE
    return rule


@router.delete("/guilds/{guild_id}/auto-moderation/rules/{rule_id}", status_code=204)
async def delete_automod_rule(guild_id: str, rule_id: str, bot: User = Depends(require_bot)):
    rule = _require_rule(guild_id, rule_id)
    name = rule.get("name")
    WORLD.automod_rules.pop(rule_id, None)
    if rule_id in WORLD.guild_automod_rules.get(guild_id, []):
        WORLD.guild_automod_rules[guild_id].remove(rule_id)
    WORLD.bus.publish("AUTO_MODERATION_RULE_DELETE", rule)
    audit.log(  # AUTO_MOD_RULE_DELETE
        guild_id, bot.id, rule_id, 142,
        changes=[{"key": "name", "old_value": name}],
    )

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Form, Header, HTTPException

from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()


# --- Helpers --------------------------------------------------------------

def _parse_bearer(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": 0, "message": "401: Unauthorized"})
    token = authorization[7:].strip()
    grant = WORLD.oauth_tokens.get(token)
    if not grant:
        raise HTTPException(status_code=401, detail={"code": 0, "message": "401: Unauthorized"})
    return grant


def _issue_tokens(application_id: str, user_id: str | None, scope: str) -> dict:
    access_token = secrets.token_urlsafe(24)
    refresh_token = secrets.token_urlsafe(24)
    grant = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "application_id": application_id,
        "user_id": user_id,
        "scope": scope,
        "expires_in": 604800,
    }
    WORLD.oauth_tokens[access_token] = grant
    return grant


# --- Token / identity -----------------------------------------------------

@router.get("/oauth2/@me")
async def oauth2_me(authorization: str | None = Header(default=None)) -> dict:
    grant = _parse_bearer(authorization)
    app = WORLD.applications.get(grant.get("application_id", ""))
    user_id = grant.get("user_id")
    user = WORLD.users.get(user_id) if user_id else None
    return {
        "application": app.to_dict(WORLD.users) if app else None,
        "scopes": (grant.get("scope") or "").split(),
        "expires": "2099-01-01T00:00:00+00:00",
        "user": user.to_dict() if user else None,
    }


@router.post("/oauth2/token")
async def oauth2_token(
    grant_type: str = Form(...),
    code: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
    scope: str | None = Form(default=None),
) -> dict:
    if grant_type == "authorization_code":
        if not code:
            raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
        auth = WORLD.oauth_authorizations.pop(code, None)
        if not auth:
            raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid code"})
        grant = _issue_tokens(
            application_id=auth.get("application_id") or client_id or "",
            user_id=auth.get("user_id"),
            scope=auth.get("scope") or scope or "",
        )
        return {
            "access_token": grant["access_token"],
            "token_type": "Bearer",
            "expires_in": grant["expires_in"],
            "refresh_token": grant["refresh_token"],
            "scope": grant["scope"],
        }

    if grant_type == "client_credentials":
        grant = _issue_tokens(
            application_id=client_id or "",
            user_id=None,
            scope=scope or "identify",
        )
        return {
            "access_token": grant["access_token"],
            "token_type": "Bearer",
            "expires_in": grant["expires_in"],
            "scope": grant["scope"],
        }

    if grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
        old = None
        old_access = None
        for access, g in WORLD.oauth_tokens.items():
            if g.get("refresh_token") == refresh_token:
                old = g
                old_access = access
                break
        if not old:
            raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid refresh_token"})
        if old_access:
            WORLD.oauth_tokens.pop(old_access, None)
        grant = _issue_tokens(
            application_id=old.get("application_id", ""),
            user_id=old.get("user_id"),
            scope=old.get("scope", ""),
        )
        return {
            "access_token": grant["access_token"],
            "token_type": "Bearer",
            "expires_in": grant["expires_in"],
            "refresh_token": grant["refresh_token"],
            "scope": grant["scope"],
        }

    raise HTTPException(status_code=400, detail={"code": 50035, "message": "Unsupported grant_type"})


@router.post("/oauth2/token/revoke")
async def oauth2_token_revoke(
    token: str = Form(...),
    token_type_hint: str | None = Form(default=None),
) -> dict:
    # Try access token first
    if token in WORLD.oauth_tokens:
        WORLD.oauth_tokens.pop(token, None)
        return {}
    # Fall back to refresh token scan
    for access, g in list(WORLD.oauth_tokens.items()):
        if g.get("refresh_token") == token:
            WORLD.oauth_tokens.pop(access, None)
            break
    return {}


@router.post("/oauth2/authorize")
async def oauth2_authorize(body: dict, bot: User = Depends(require_bot)) -> dict:
    client_id = body.get("client_id")
    scope = body.get("scope", "")
    redirect_uri = body.get("redirect_uri", "")
    state = body.get("state")
    response_type = body.get("response_type", "code")
    if not client_id or not redirect_uri:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid Form Body"})
    code = secrets.token_urlsafe(24)
    WORLD.oauth_authorizations[code] = {
        "application_id": client_id,
        "user_id": bot.id,
        "scope": scope,
        "redirect_uri": redirect_uri,
        "response_type": response_type,
    }
    sep = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}"
    if state:
        location += f"&state={state}"
    return {"location": location}


# --- Connections / role-connections --------------------------------------

@router.get("/users/@me/connections")
async def list_my_connections(_bot: User = Depends(require_bot)) -> list[dict]:
    return []


def _role_conn_key(user_id: str, application_id: str) -> str:
    return f"role_conn:{user_id}:{application_id}"


@router.get("/users/@me/applications/{application_id}/role-connection")
async def get_role_connection(application_id: str, bot: User = Depends(require_bot)) -> dict:
    key = _role_conn_key(bot.id, application_id)
    existing = WORLD.oauth_authorizations.get(key)
    if existing:
        return existing
    return {"platform_name": None, "platform_username": None, "metadata": {}}


@router.put("/users/@me/applications/{application_id}/role-connection")
async def put_role_connection(application_id: str, body: dict, bot: User = Depends(require_bot)) -> dict:
    key = _role_conn_key(bot.id, application_id)
    payload = {
        "platform_name": body.get("platform_name"),
        "platform_username": body.get("platform_username"),
        "metadata": body.get("metadata") or {},
    }
    WORLD.oauth_authorizations[key] = payload
    return payload


def _role_conn_metadata_key(application_id: str) -> str:
    return f"role_conn_metadata:{application_id}"


@router.get("/applications/{application_id}/role-connections/metadata")
async def get_role_connection_metadata(application_id: str, _bot: User = Depends(require_bot)) -> list[dict]:
    key = _role_conn_metadata_key(application_id)
    entry = WORLD.oauth_authorizations.get(key)
    if not entry:
        return []
    return entry.get("records", [])


@router.post("/applications/{application_id}/role-connections/metadata")
async def post_role_connection_metadata(application_id: str, body: Any, _bot: User = Depends(require_bot)) -> list[dict]:
    records = body if isinstance(body, list) else []
    WORLD.oauth_authorizations[_role_conn_metadata_key(application_id)] = {"records": records}
    return records


@router.put("/applications/{application_id}/role-connections/metadata")
async def put_role_connection_metadata(application_id: str, body: Any, _bot: User = Depends(require_bot)) -> list[dict]:
    records = body if isinstance(body, list) else []
    WORLD.oauth_authorizations[_role_conn_metadata_key(application_id)] = {"records": records}
    return records

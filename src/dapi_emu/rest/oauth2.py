from __future__ import annotations

import html
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import require_bot
from ..snowflake import generate as new_snowflake
from ..state import WORLD, User

router = APIRouter()

_TEMPLATE_PATH = Path(__file__).parent.parent / "panel" / "templates" / "oauth2_authorize.html"


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


def _append_query(url: str, params: dict[str, str]) -> str:
    """Append query params to an URL, preserving any existing query string."""
    parts = [f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v is not None]
    if not parts:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + "&".join(parts)


def _render_authorize_page(
    *,
    app_name: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str | None,
    permissions: str | None,
    confirm_path: str,
) -> str:
    tpl = _TEMPLATE_PATH.read_text(encoding="utf-8")
    scopes_list = [s for s in scope.split() if s]
    scope_items = "".join(f"<li>{html.escape(s)}</li>" for s in scopes_list) or "<li>identify</li>"

    user_options_parts: list[str] = []
    for u in WORLD.users.values():
        if u.bot:
            continue
        label = u.global_name or u.username
        user_options_parts.append(
            f'<option value="{html.escape(u.id)}">{html.escape(label)} ({html.escape(u.username)})</option>'
        )
    if not user_options_parts:
        user_options_parts.append('<option value="" disabled>ユーザーが存在しません</option>')
    user_options = "".join(user_options_parts)

    want_bot = "bot" in scopes_list
    if want_bot:
        guild_opts: list[str] = []
        for g in WORLD.guilds.values():
            guild_opts.append(
                f'<option value="{html.escape(g.id)}">{html.escape(g.name)}</option>'
            )
        if not guild_opts:
            guild_opts.append('<option value="" disabled>サーバーが存在しません</option>')
        guild_field = (
            '<div class="field">'
            '<label for="guild_id">Bot を追加するサーバー</label>'
            '<select id="guild_id" name="guild_id">'
            + "".join(guild_opts)
            + "</select></div>"
        )
    else:
        guild_field = ""

    initial = (app_name[:1] or "?").upper()

    return (
        tpl.replace("__APP_NAME__", html.escape(app_name))
        .replace("__APP_INITIAL__", html.escape(initial))
        .replace("__CONFIRM_PATH__", html.escape(confirm_path))
        .replace("__SCOPE_ITEMS__", scope_items)
        .replace("__USER_OPTIONS__", user_options)
        .replace("__GUILD_FIELD__", guild_field)
        .replace("__CLIENT_ID__", html.escape(client_id))
        .replace("__REDIRECT_URI__", html.escape(redirect_uri))
        .replace("__SCOPE__", html.escape(scope))
        .replace("__STATE__", html.escape(state or ""))
        .replace("__PERMISSIONS__", html.escape(permissions or ""))
    )


@router.get("/oauth2/authorize", response_class=HTMLResponse)
async def oauth2_authorize_page(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query("code"),
    scope: str = Query(""),
    state: str | None = Query(default=None),
    permissions: str | None = Query(default=None),
    guild_id: str | None = Query(default=None),
) -> HTMLResponse:
    app = WORLD.applications.get(client_id)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})

    # The confirm endpoint is mounted under the same prefix as this router
    # (/api/v10 or /api). We reconstruct from our own path so it matches.
    # Simpler: rely on the fact that forms post to a relative path.
    body = _render_authorize_page(
        app_name=app.name,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        permissions=permissions,
        confirm_path="authorize/confirm",
    )
    return HTMLResponse(body)


@router.post("/oauth2/authorize/confirm")
async def oauth2_authorize_confirm(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form(""),
    state: str | None = Form(default=None),
    user_id: str = Form(...),
    guild_id: str | None = Form(default=None),
    permissions: str | None = Form(default=None),
    action: str = Form("approve"),
) -> RedirectResponse:
    if action == "deny":
        params: dict[str, str] = {"error": "access_denied"}
        if state:
            params["state"] = state
        return RedirectResponse(_append_query(redirect_uri, params), status_code=302)

    app = WORLD.applications.get(client_id)
    if not app:
        raise HTTPException(status_code=404, detail={"code": 10002, "message": "Unknown Application"})
    if user_id not in WORLD.users:
        raise HTTPException(status_code=400, detail={"code": 50035, "message": "Invalid user_id"})

    code = secrets.token_urlsafe(24)
    WORLD.oauth_authorizations[code] = {
        "application_id": client_id,
        "user_id": user_id,
        "scope": scope,
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }

    scopes = scope.split()
    if "bot" in scopes and guild_id and guild_id in WORLD.guilds:
        if (app.bot_id, guild_id) not in WORLD.members:
            from .. import system_messages
            WORLD.add_member(app.bot_id, guild_id)
            payload = WORLD.members[(app.bot_id, guild_id)].to_dict(WORLD.users) | {"guild_id": guild_id}
            WORLD.bus.publish("GUILD_MEMBER_ADD", payload)
            system_messages.member_joined(guild_id, app.bot_id)

    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(_append_query(redirect_uri, params), status_code=302)


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

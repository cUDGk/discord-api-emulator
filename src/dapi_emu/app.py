"""FastAPI application factory."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from . import config
from .gateway.server import router as gateway_router
from .panel.routes import router as panel_router
from .ratelimit import RateLimitHeadersMiddleware
from .rest.applications import router as applications_router
from .rest.channels import router as channels_router
from .rest.gateway import router as gateway_rest_router
from .rest.guilds import router as guilds_router
from .rest.users import router as users_router

log = logging.getLogger("dapi")


def create_app() -> FastAPI:
    app = FastAPI(title="dapi-emu", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(RateLimitHeadersMiddleware)

    # Gateway WS (top-level, not /api)
    app.include_router(gateway_router)

    # Panel + admin (top-level)
    app.include_router(panel_router)

    # Discord REST surface. Mount at both /api/v10/... and /api/... to be tolerant.
    for prefix in (f"/api/v{config.API_VERSION}", "/api"):
        app.include_router(gateway_rest_router, prefix=prefix)
        app.include_router(users_router, prefix=prefix)
        app.include_router(applications_router, prefix=prefix)
        app.include_router(channels_router, prefix=prefix)
        app.include_router(guilds_router, prefix=prefix)
        # Lazily loaded routers — imported here so Phase-2+ modules wire in cleanly.
        _mount_phase2plus(app, prefix)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            body = detail
        else:
            body = {"code": 0, "message": str(detail)}
        return JSONResponse(body, status_code=exc.status_code)

    @app.get("/")
    async def root() -> dict:
        return {"name": "dapi-emu", "panel": "/panel", "gateway": config.GATEWAY_WS_URL}

    return app


def _mount_phase2plus(app: FastAPI, prefix: str) -> None:
    """Mount optional routers if their modules exist (Phase 2+)."""
    module_names = [
        "interactions", "threads", "webhooks", "dm", "emojis", "stickers",
        "invites", "bans", "audit_log", "voice", "scheduled_events",
        "stage_instances", "automod", "polls", "oauth2", "cdn", "entitlements",
        "skus", "teams", "pins",
    ]
    for name in module_names:
        try:
            module = __import__(f"dapi_emu.rest.{name}", fromlist=["router"])
        except ModuleNotFoundError:
            continue
        except Exception as e:  # import error shouldn't crash the whole app
            log.warning("failed to import rest.%s: %s", name, e)
            continue
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router, prefix=prefix)

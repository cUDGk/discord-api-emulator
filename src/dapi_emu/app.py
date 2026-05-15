"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from . import config, persistence
from .gateway.server import router as gateway_router
from .gateway.voice import router as voice_gateway_router
from .panel.routes import router as panel_router
from .workbench.routes import router as workbench_router
from .ratelimit import RateLimitHeadersMiddleware
from .rest.applications import router as applications_router
from .rest.channels import router as channels_router
from .rest.gateway import router as gateway_rest_router
from .rest.guilds import router as guilds_router
from .rest.users import router as users_router
from .state import WORLD

log = logging.getLogger("dapi")


def create_app() -> FastAPI:
    app = FastAPI(title="dapi-emu", version="0.1.0")

    _autosave_task: asyncio.Task | None = None

    @app.on_event("startup")
    async def _startup() -> None:
        nonlocal _autosave_task
        if persistence.is_enabled():
            db_path = os.environ["DAPI_DB_PATH"]
            persistence.init_db(db_path)
            persistence.load_world(WORLD, db_path)
            _autosave_task = await persistence.start_autosave(WORLD, db_path)
            log.info("persistence: loaded from %s, autosaving", db_path)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if _autosave_task is not None:
            _autosave_task.cancel()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(RateLimitHeadersMiddleware)

    # Gateway WS (top-level, not /api)
    app.include_router(gateway_router)
    app.include_router(voice_gateway_router)

    # Panel + admin (top-level)
    app.include_router(panel_router)
    # Workbench: interaction tester / recorder / replayer
    app.include_router(workbench_router)

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
    # webhooks must mount before interactions — they share the
    # `/webhooks/{id}/{token}` path shape. The webhooks router has a built-in
    # fallback to interaction tokens so both flows are handled.
    module_names = [
        "webhooks", "interactions", "threads", "dm", "emojis", "stickers",
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

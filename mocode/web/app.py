"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mocode.core.orchestrator import MocodeCore

from .permission import WebPermissionHandler
from .routers import chat, sessions, config, permission

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create MocodeCore on startup, cleanup on shutdown."""
    perm_handler = WebPermissionHandler()
    core = MocodeCore(
        persistence=True,
        permission_handler=perm_handler,
    )
    app.state.core = core
    app.state.permission_handler = perm_handler
    logger.info(
        "MoCode web backend started (model=%s, provider=%s)",
        core.current_model,
        core.current_provider,
    )
    yield
    perm_handler.cancel_all()
    if core.has_unsaved_changes:
        try:
            core.save_session()
        except Exception:
            pass
    logger.info("MoCode web backend stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="MoCode",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(config.router)
    app.include_router(permission.router)

    return app

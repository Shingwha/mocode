"""FastAPI application factory (v0.2)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..app import AppBuilder
from ..permission import DefaultPermissionHandler
from ..session import FileSessionStore
from .routers import chat, sessions, config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create App on startup, cleanup on shutdown."""
    mocode_app = (
        AppBuilder()
        .with_session_store(FileSessionStore())
        .with_permission_handler(DefaultPermissionHandler())
        .build()
    )
    mocode_app.sessions._dirty = True  # mark so first save works
    app.state.app = mocode_app
    logger.info(
        "MoCode web backend started (model=%s, provider=%s)",
        mocode_app.current_model,
        mocode_app.current_provider,
    )
    yield
    if mocode_app.has_unsaved_changes:
        try:
            mocode_app.save_session()
        except Exception:
            pass
    logger.info("MoCode web backend stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="MoCode",
        version="0.2.0",
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

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app

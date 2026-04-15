"""FastAPI dependency injection."""

from fastapi import Request

from mocode.core.orchestrator import MocodeCore
from .permission import WebPermissionHandler


def get_core(request: Request) -> MocodeCore:
    """Get MocodeCore instance from app state."""
    return request.app.state.core


def get_permission_handler(request: Request) -> WebPermissionHandler:
    """Get WebPermissionHandler from app state."""
    return request.app.state.permission_handler

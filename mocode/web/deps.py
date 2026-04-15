"""FastAPI dependency injection."""

from mocode.core.orchestrator import MocodeCore
from .permission import WebPermissionHandler


def get_core(request) -> MocodeCore:
    """Get MocodeCore instance from app state."""
    return request.app.state.core


def get_permission_handler(request) -> WebPermissionHandler:
    """Get WebPermissionHandler from app state."""
    return request.app.state.permission_handler

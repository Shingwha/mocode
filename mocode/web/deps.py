"""FastAPI dependency injection (v0.2)."""

from fastapi import Request

from ..app import App


def get_app(request: Request) -> App:
    """Get App instance from app state."""
    return request.app.state.app

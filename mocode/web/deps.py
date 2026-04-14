"""FastAPI dependency injection."""

from mocode.core.orchestrator import MocodeCore


def get_core(request) -> MocodeCore:
    """Get MocodeCore instance from app state."""
    return request.app.state.core

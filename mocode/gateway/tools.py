"""Gateway-specific tools for sending files to users"""

from contextvars import ContextVar

from ..tools.base import Tool, ToolRegistry
from ..core.orchestrator import MocodeCore

# Context variable set by manager before each core.chat() call
_current_core: ContextVar[MocodeCore | None] = ContextVar(
    "_current_core", default=None
)


def register_gateway_tools() -> None:
    """Register gateway-only tools. Called once at gateway startup."""

    def send_file_handler(args: dict) -> str:
        path = args.get("path", "")
        if not path:
            return "Error: path is required"
        core = _current_core.get()
        if core is None:
            return "Error: send_file only works in gateway mode"
        if not hasattr(core, "_pending_media"):
            core._pending_media = []
        core._pending_media.append(path)
        return f"File queued for sending: {path}"

    ToolRegistry.register(Tool(
        name="send_file",
        description="Send a file to the user. Use this to deliver generated images, documents, or other files.",
        params={"path": {"type": "string", "description": "Absolute path to the file to send"}},
        func=send_file_handler,
    ))

"""Gateway-specific tools for sending files to users"""

from contextvars import ContextVar

from ..tools.base import Tool, ToolRegistry


class PendingMedia:
    """Collects media file paths queued by send_file during a chat call."""

    def __init__(self) -> None:
        self.paths: list[str] = []


# Context variables set by manager before each core.chat() call
_current_core = ContextVar("_current_core", default=None)
_current_media: ContextVar[PendingMedia | None] = ContextVar(
    "_current_media", default=None
)


def register_gateway_tools() -> None:
    """Register gateway-only tools. Called once at gateway startup."""

    def send_file_handler(args: dict) -> str:
        path = args.get("path", "")
        if not path:
            return "Error: path is required"
        media = _current_media.get()
        if media is None:
            return "Error: send_file only works in gateway mode"
        media.paths.append(path)
        return f"File queued for sending: {path}"

    ToolRegistry.register(Tool(
        name="send_file",
        description="Send a file to the user. Use this to deliver generated images, documents, or other files.",
        params={"path": {"type": "string", "description": "Absolute path to the file to send"}},
        func=send_file_handler,
    ))

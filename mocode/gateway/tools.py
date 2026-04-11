"""Gateway-specific tools for sending files to users"""

from contextvars import ContextVar
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator

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
# Context variables shared with cron tools
_current_scheduler = ContextVar("_current_scheduler", default=None)
_current_session_key: ContextVar[str] = ContextVar(
    "_current_session_key", default=""
)
_current_channel: ContextVar[str] = ContextVar(
    "_current_channel", default=""
)
_current_chat_id: ContextVar[str] = ContextVar(
    "_current_chat_id", default=""
)


@dataclass
class ChatContext:
    """Values needed to set up the gateway tool context for a chat call."""
    core: object
    scheduler: object | None
    session_key: str
    channel: str
    chat_id: str


@asynccontextmanager
async def chat_session(ctx: ChatContext) -> AsyncGenerator[PendingMedia, None]:
    """Async context manager that sets/resets all gateway ContextVars."""
    pending = PendingMedia()
    tokens = [
        _current_core.set(ctx.core),
        _current_media.set(pending),
        _current_scheduler.set(ctx.scheduler),
        _current_session_key.set(ctx.session_key),
        _current_channel.set(ctx.channel),
        _current_chat_id.set(ctx.chat_id),
    ]
    try:
        yield pending
    finally:
        for token in tokens:
            token.var.reset(token)


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

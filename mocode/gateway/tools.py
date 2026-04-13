"""Gateway-specific tools for sending files to users"""

import asyncio
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
# Event loop for async bridge in compact/dream handlers
_current_loop: ContextVar = ContextVar("_current_loop", default=None)

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
        _current_loop.set(asyncio.get_running_loop()),
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

    def compact_handler(args: dict) -> str:
        core = _current_core.get()
        if core is None:
            return "Error: compact only works in gateway mode"
        loop = _current_loop.get()
        if loop is None:
            return "Error: event loop not available"
        msg_count = len(core.agent.messages)
        if msg_count < 4:
            return f"Not enough messages to compact ({msg_count}, need at least 4)."
        future = asyncio.run_coroutine_threadsafe(core.compact(), loop)
        try:
            result = future.result(timeout=120)
        except asyncio.TimeoutError:
            return "Error: compact timed out after 120 seconds"
        except Exception as e:
            return f"Error: compact failed: {e}"
        old = result.get("old_count", "?")
        new = result.get("new_count", "?")
        return f"Compacted: {old} -> {new} messages"

    ToolRegistry.register(Tool(
        name="compact",
        description="Compress conversation history into a summary to save tokens. Call this when the conversation is getting long or before starting a new topic.",
        params={},
        func=compact_handler,
    ))

    def dream_handler(args: dict) -> str:
        core = _current_core.get()
        if core is None:
            return "Error: dream only works in gateway mode"
        loop = _current_loop.get()
        if loop is None:
            return "Error: event loop not available"
        future = asyncio.run_coroutine_threadsafe(core.dream(), loop)
        try:
            result = future.result(timeout=120)
        except asyncio.TimeoutError:
            return "Error: dream timed out after 120 seconds"
        except Exception as e:
            return f"Error: dream failed: {e}"
        if result.get("skipped"):
            return "Dream skipped: no new summaries to process"
        summaries = result.get("summaries_processed", 0)
        edits = result.get("edits_made", 0)
        return f"Dream complete: {summaries} summaries processed, {edits} edits made"

    ToolRegistry.register(Tool(
        name="dream",
        description="Trigger a dream cycle to consolidate conversation summaries into long-term memory files. Call this after significant work has been done to update memory.",
        params={},
        func=dream_handler,
    ))

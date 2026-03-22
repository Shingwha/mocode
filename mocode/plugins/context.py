"""Plugin Context - Controlled access to core components"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Awaitable

if TYPE_CHECKING:
    from ..core.events import EventBus, EventType


@dataclass
class PluginContext:
    """Context provided to plugins for controlled access to core components

    This class provides a safe interface for plugins to interact with
    the core system without direct access to internal components.
    """

    # Event system
    event_bus: "EventBus"
    on_event: Callable[["EventType", Callable], None]

    # Message injection (with queue)
    inject_message: Callable[[str, str | None], Awaitable[str]]
    queue_message: Callable[[str, str | None], None]

    # State access (read-only)
    get_messages: Callable[[], list[dict]]
    workdir: str
    is_agent_busy: Callable[[], bool]

    # Conversation tracking
    current_conversation_id: str | None = None

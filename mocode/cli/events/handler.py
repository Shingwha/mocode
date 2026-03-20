"""CLI event handler - centralizes CLI event processing"""

from ...core import EventBus, EventType, preview_result
from ..ui.layout import Layout


class CLIEventHandler:
    """CLI event processor.

    Subscribes to all relevant events and updates the Layout accordingly.
    """

    def __init__(self, layout: Layout):
        self._layout = layout

    def setup(self, event_bus: EventBus) -> None:
        """Subscribe to all events."""
        event_bus.on(EventType.MESSAGE_ADDED, self._on_message_added)
        event_bus.on(EventType.TEXT_COMPLETE, self._on_text_complete)
        event_bus.on(EventType.TOOL_START, self._on_tool_start)
        event_bus.on(EventType.TOOL_COMPLETE, self._on_tool_complete)
        event_bus.on(EventType.ERROR, self._on_error)
        event_bus.on(EventType.INTERRUPTED, self._on_interrupted)

    def teardown(self, event_bus: EventBus) -> None:
        """Unsubscribe from all events."""
        event_bus.off(EventType.MESSAGE_ADDED, self._on_message_added)
        event_bus.off(EventType.TEXT_COMPLETE, self._on_text_complete)
        event_bus.off(EventType.TOOL_START, self._on_tool_start)
        event_bus.off(EventType.TOOL_COMPLETE, self._on_tool_complete)
        event_bus.off(EventType.ERROR, self._on_error)
        event_bus.off(EventType.INTERRUPTED, self._on_interrupted)

    def _on_message_added(self, event) -> None:
        """User message added - start thinking animation."""
        self._layout.set_thinking(True, "Thinking")

    def _on_text_complete(self, event) -> None:
        """Text complete - stop thinking and show response."""
        self._layout.set_thinking(False)
        self._layout.add_assistant_message(event.data)

    def _on_tool_start(self, event) -> None:
        """Tool started - stop thinking and show tool call."""
        self._layout.set_thinking(False)

        name = event.data["name"]
        args = event.data["args"]
        preview = str(list(args.values())[0])[:50] if args else ""
        self._layout.add_tool_call(name, preview)

    def _on_tool_complete(self, event) -> None:
        """Tool complete - show result preview."""
        result = preview_result(event.data["result"])
        self._layout.add_tool_result(result)

    def _on_error(self, event) -> None:
        """Error occurred - stop thinking and show error."""
        self._layout.set_thinking(False)
        self._layout.add_error_message(str(event.data))

    def _on_interrupted(self, event) -> None:
        """Interrupted - stop thinking and show message."""
        self._layout.set_thinking(False)
        # If tool was denied or interrupted, message already shown
        if event.data and event.data.get("reason") in ("denied", "interrupted"):
            return
        self._layout.add_assistant_message("[interrupted]")

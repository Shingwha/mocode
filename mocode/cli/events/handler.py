"""CLI event handler - centralizes CLI event processing"""

from ...core import EventBus, EventType, preview_result
from ..ui.display import Display


class CLIEventHandler:
    """CLI event processor.

    Subscribes to all relevant events and updates the Display accordingly.
    """

    def __init__(self, display: Display):
        self._display = display

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
        self._display.set_thinking(True, "Thinking")

    def _on_text_complete(self, event) -> None:
        self._display.set_thinking(False)
        if isinstance(event.data, dict):
            content = event.data.get("content", "")
        else:
            content = event.data
        self._display.assistant_message(content)

    def _on_tool_start(self, event) -> None:
        self._display.set_thinking(False)
        name = event.data["name"]
        args = event.data["args"]
        preview = str(list(args.values())[0])[:50] if args else ""
        self._display.tool_call(name, preview)

    def _on_tool_complete(self, event) -> None:
        result = preview_result(event.data["result"])
        self._display.tool_result(result)

    def _on_error(self, event) -> None:
        self._display.set_thinking(False)
        self._display.error(str(event.data))

    def _on_interrupted(self, event) -> None:
        self._display.set_thinking(False)
        if event.data and event.data.get("reason") in ("denied", "interrupted"):
            return
        self._display.assistant_message("[interrupted]")

"""SSE event bridge - converts EventBus events to SSE stream (v0.2)."""

import asyncio
import json
import logging

from ..event import EventBus, EventType

logger = logging.getLogger(__name__)


class SSEEventBridge:
    """Bridge EventBus events to SSE stream via asyncio.Queue."""

    def __init__(self, event_bus: EventBus):
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._event_bus = event_bus
        self._handlers: list[tuple[EventType, callable]] = []
        self._tool_seq = 0
        self._active_tool_id: str | None = None

    def attach(self):
        """Subscribe to EventBus events."""
        mapping = {
            EventType.TEXT_COMPLETE: self._on_text_complete,
            EventType.REASONING: self._on_reasoning,
            EventType.TOOL_START: self._on_tool_start,
            EventType.TOOL_COMPLETE: self._on_tool_complete,
            EventType.INTERRUPTED: self._on_interrupted,
        }
        for etype, handler in mapping.items():
            self._event_bus.on(etype, handler)
            self._handlers.append((etype, handler))

    def detach(self):
        """Unsubscribe from EventBus events."""
        for etype, handler in self._handlers:
            self._event_bus.off(etype, handler)
        self._handlers.clear()

    async def events(self):
        """Async generator yielding SSE formatted strings."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=30)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:
                return
            yield event

    def push(self, event_type: str, data: dict):
        """Push an SSE event to the queue."""
        self._queue.put_nowait(
            f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        )

    def send_done(self, response: str):
        """Send done event and stop the stream."""
        self.push("done", {"response": response})
        self._queue.put_nowait(None)

    def send_error(self, message: str):
        """Send error event and stop the stream."""
        self.push("error", {"message": message})
        self._queue.put_nowait(None)

    def stop(self):
        """Stop the stream without done/error event."""
        self._queue.put_nowait(None)

    # -- Event handlers --

    def _next_tool_id(self) -> str:
        self._tool_seq += 1
        return f"tc_{self._tool_seq}"

    def _on_text_complete(self, event):
        data = event.data or {}
        self.push("text_complete", {"content": data.get("content", "")})

    def _on_reasoning(self, event):
        data = event.data or {}
        self.push("reasoning", {"content": data.get("content", "")})

    def _on_tool_start(self, event):
        data = event.data or {}
        tid = self._next_tool_id()
        self._active_tool_id = tid
        self.push("tool_start", {
            "id": tid,
            "name": data.get("name", ""),
            "args": data.get("args", {}),
        })

    def _on_tool_complete(self, event):
        data = event.data or {}
        self.push("tool_complete", {
            "id": self._active_tool_id,
            "name": data.get("name", ""),
            "result": data.get("result", ""),
        })
        self._active_tool_id = None

    def _on_interrupted(self, event):
        if self._active_tool_id is not None:
            self.push("tool_complete", {
                "id": self._active_tool_id,
                "name": "",
                "result": "[interrupted]",
            })
            self._active_tool_id = None
        self.push("interrupted", event.data or {})

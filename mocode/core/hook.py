"""Hooks — unified async hook system with priority ordering.

All hooks are interceptors: handlers receive data and may return modified data.
Return None (or no return) to pass data through unchanged.

Hook names as string constants — extensions can define their own.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Union

Handler = Union[Callable[[Any], Any], Callable[[Any], Awaitable[Any]]]

# Hook name constants
PRE_LOOP = "pre_loop"
MESSAGE_ADDED = "message_added"
TOOL_START = "tool_start"
TOOL_COMPLETE = "tool_complete"
TEXT_COMPLETE = "text_complete"
USAGE_UPDATE = "usage_update"
CONTEXT_COMPACT = "context_compact"
ERROR = "error"


class Hooks:
    """Unified hook system — sequential, async, priority-ordered."""

    def __init__(self):
        self._hooks: dict[str, list[tuple[int, Handler]]] = {}

    def on(self, name: str, handler: Handler, priority: int = 50) -> Hooks:
        self._hooks.setdefault(name, []).append((priority, handler))
        self._hooks[name].sort(key=lambda x: x[0])
        return self

    def off(self, name: str, handler: Handler) -> Hooks:
        if name in self._hooks:
            self._hooks[name] = [
                (p, h) for p, h in self._hooks[name] if h is not handler
            ]
        return self

    async def emit(self, name: str, data: Any = None) -> Any:
        """Call handlers sequentially. Returns final data (possibly modified)."""
        result = data
        for _, handler in self._hooks.get(name, []):
            try:
                r = handler(result)
                if inspect.iscoroutine(r):
                    r = await r
                if r is not None:
                    result = r
            except Exception:
                pass
        return result

    def clear(self) -> None:
        self._hooks.clear()

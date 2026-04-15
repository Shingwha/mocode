"""Web permission handler - suspends agent and waits for web client response."""

import asyncio
import logging
from uuid import uuid4

from mocode.core.permission import PermissionHandler

from .events import SSEEventBridge

logger = logging.getLogger(__name__)


class WebPermissionHandler(PermissionHandler):
    """Permission handler that suspends agent and waits for web client response."""

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}
        self._bridge: SSEEventBridge | None = None

    def set_bridge(self, bridge: SSEEventBridge | None):
        self._bridge = bridge

    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        request_id = f"perm_{uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future

        if self._bridge:
            self._bridge.push("permission_ask", {
                "request_id": request_id,
                "tool": tool_name,
                "tool_name": tool_name,
                "args": tool_args,
                "description": f"{tool_name}: {_describe(tool_name, tool_args)}",
            })

        try:
            result = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            logger.warning("Permission request %s timed out", request_id)
            self._pending.pop(request_id, None)
            return "deny"

        self._pending.pop(request_id, None)

        if self._bridge:
            self._bridge.push("permission_resolved", {
                "request_id": request_id,
                "approved": result == "allow",
            })

        return result

    def resolve(self, request_id: str, response: str) -> bool:
        """Resolve a pending permission request. Returns False if not found."""
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(response)
            return True
        return False

    def cancel_all(self):
        """Cancel all pending permission requests."""
        for future in self._pending.values():
            if not future.done():
                future.set_result("deny")
        self._pending.clear()


def _describe(tool_name: str, tool_args: dict) -> str:
    if tool_name == "bash":
        return tool_args.get("command", "")
    return str(tool_args)[:100]

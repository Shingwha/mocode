"""Workflow node hooks — per-node lifecycle hooks for AgentLoop execution.

Contains _WorkflowNodeHook, a lightweight AgentHook that captures tool calls
within a node agent and emits NodeToolCallEvent / NodeToolBatchDoneEvent.
"""

from __future__ import annotations

from ...core.hook import ToolTimingTracker
from ..utils import group_tool_calls
from .events import NodeToolBatchDoneEvent, NodeToolCallEvent


class _WorkflowNodeHook:
    """Lightweight hook that captures tool calls within a node agent
    and emits NodeToolCallEvent / NodeToolBatchDoneEvent via on_event."""

    def __init__(self, node_id: str, on_event):
        self._node_id = node_id
        self._on_event = on_event
        # Per-batch tracking (reset on each on_response)
        self._groups: list[tuple[str, list[str]]] = []
        self._tracker = ToolTimingTracker()

    async def on_response(self, ctx) -> None:
        if ctx.response and ctx.response.tool_calls:
            self._groups = group_tool_calls(ctx.response.tool_calls)
            self._tracker.reset()

    async def on_tool_start(self, ctx) -> None:
        self._tracker.start(ctx.tool_call_id)

    async def on_tool_complete(self, ctx) -> None:
        elapsed = self._tracker.complete(
            ctx.tool_call_id, ctx.tool_name, ctx.tool_error, ctx.tool_timeout
        )
        self._on_event(NodeToolCallEvent(
            node_id=self._node_id,
            tool_name=ctx.tool_name,
            tool_args=ctx.tool_args,
            error=self._tracker.errors.get(ctx.tool_name),
            elapsed=elapsed,
        ))

    async def after_tools(self, ctx) -> None:
        self._on_event(NodeToolBatchDoneEvent(
            node_id=self._node_id,
            groups=list(self._groups),
            errors=dict(self._tracker.errors),
            elapsed=dict(self._tracker.elapsed),
        ))

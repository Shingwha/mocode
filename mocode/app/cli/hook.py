"""CLIDisplayHook — bridges AgentHook lifecycle to the CLI Display."""

from __future__ import annotations

from ...core.hook import (
    AgentHook,
    IterationContext,
    ToolCallContext,
    ToolTimingTracker,
)
from ...core.tool import ToolRegistry
from .display import group_tool_calls, merge_summaries
from .spinner import Priority, Truncate


class CLIDisplayHook(AgentHook):
    """Renders tool calls, reasoning, and usage to the terminal.

    Tool calls update the spinner while running; one status line per tool name
    is printed once the whole batch completes.
    """

    def __init__(self, display, tools: ToolRegistry):
        self._d = display
        self._tools = tools
        self._prompt = self._completion = 0
        self._tool_groups: list[tuple[str, list[str]]] = []
        self._tracker = ToolTimingTracker()

    async def on_response(self, ctx: IterationContext) -> None:
        if ctx.reasoning_content and not (ctx.response and ctx.response.tool_calls):
            self._d.reasoning(ctx.reasoning_content)
        if ctx.final_content and ctx.response and ctx.response.tool_calls:
            self._d.text_response(ctx.final_content)
        if ctx.usage:
            self._prompt += ctx.usage.prompt_tokens
            self._completion += ctx.usage.completion_tokens
        if ctx.response and ctx.response.tool_calls:
            groups = group_tool_calls(ctx.response.tool_calls, self._tools)
            self._tool_groups = groups
            self._tracker.reset()
            self._update_spinner_for_tools(groups)

    async def after_iteration(self, ctx: IterationContext) -> None:
        if self._prompt or self._completion:
            self._d.usage(self._prompt, self._completion)
            self._prompt = self._completion = 0

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        self._tracker.start(ctx.tool_call_id)

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        if ctx.status == "timeout":
            self._tracker.complete(ctx.tool_call_id, ctx.tool_name, timeout=ctx.tool_timeout)
            return
        error = None if ctx.status == "ok" else (ctx.tool_result or ctx.status)
        self._tracker.complete(ctx.tool_call_id, ctx.tool_name, error)

    async def after_tools(self, ctx: IterationContext) -> None:
        for name, summaries in self._tool_groups:
            merged = merge_summaries(summaries)
            elapsed = self._tracker.elapsed.get(name, 0)
            if name in self._tracker.errors:
                self._d.tool_fail(name, merged, self._tracker.errors[name], elapsed)
            else:
                self._d.tool_done(name, merged, elapsed)

        self._d.spinner_remove("tools_tag")
        self._d.spinner_remove("tools_detail")
        self._d.spinner_set(
            "thinking", "Thinking", priority=Priority.NORMAL, truncate=Truncate.TAIL
        )
        self._tool_groups = []
        self._tracker.reset()

    async def on_event(self, event: object) -> None:
        """Render events emitted by any hook (compaction, plugin notices, ...)."""
        self._d.render_event(event)

    # ── Internal ───────────────────────────────────────────

    def _update_spinner_for_tools(self, groups: list[tuple[str, list[str]]]) -> None:
        """Show running tools in the spinner: ``{n} tool(s) · read(a, b)``."""
        total = sum(len(summaries) for _, summaries in groups)
        label = "running 1 tool" if total == 1 else f"running {total} tools"

        parts = []
        for name, summaries in groups:
            count = len(summaries)
            if total == 1:
                parts.append(f"{name}({merge_summaries(summaries)})")
            elif count > 1:
                parts.append(f"{name}×{count}")
            else:
                parts.append(name)

        self._d.spinner_remove("thinking")
        self._d.spinner_set(
            "tools_tag", label, priority=Priority.HIGH, truncate=Truncate.TAIL
        )
        self._d.spinner_set(
            "tools_detail", ", ".join(parts), priority=Priority.LOW, truncate=Truncate.MIDDLE
        )

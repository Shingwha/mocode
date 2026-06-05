"""CLIDisplayHook — bridges AgentHook lifecycle to CLI Display."""

from __future__ import annotations

import time

from ...core.hook import AgentHook
from .display import group_tool_calls, merge_summaries
from .spinner import Priority, Truncate


class CLIDisplayHook(AgentHook):
    """Renders tool calls, reasoning, and usage to the terminal.

    Tool calls update the spinner while running; detailed status lines
    (one per tool type, with parameters) are printed after all tools complete.
    """

    def __init__(self, display):
        self._d = display
        self._prompt = self._completion = 0
        # Tool execution tracking (reset per batch)
        self._tool_groups: list[tuple[str, list[str]]] = []
        self._tool_errors: dict[str, str] = {}  # name -> first error msg
        self._tool_call_start: dict[str, float] = {}  # call_id -> monotonic
        self._tool_elapsed: dict[str, float] = {}  # name -> max elapsed

    async def on_response(self, ctx):
        if ctx.reasoning_content and not (ctx.response and ctx.response.tool_calls):
            self._d.reasoning(ctx.reasoning_content)
        if ctx.final_content and ctx.response and ctx.response.tool_calls:
            self._d.text_response(ctx.final_content)
        if ctx.usage:
            self._prompt += ctx.usage.prompt_tokens
            self._completion += ctx.usage.completion_tokens
        # Update spinner with tool info instead of printing immediately
        if ctx.response and ctx.response.tool_calls:
            groups = group_tool_calls(ctx.response.tool_calls)
            self._tool_groups = groups
            self._tool_errors = {}
            self._tool_call_start = {}
            self._tool_elapsed = {}
            self._update_spinner_for_tools(groups)

    async def after_iteration(self, ctx):
        if self._prompt or self._completion:
            self._d.usage(self._prompt, self._completion)
            self._prompt = self._completion = 0

    async def on_tool_start(self, ctx):
        self._tool_call_start[ctx.tool_call_id] = time.monotonic()

    async def on_tool_complete(self, ctx):
        # Track per-tool elapsed (max across parallel calls of same name)
        if ctx.tool_call_id in self._tool_call_start:
            elapsed = time.monotonic() - self._tool_call_start[ctx.tool_call_id]
            prev = self._tool_elapsed.get(ctx.tool_name, 0)
            self._tool_elapsed[ctx.tool_name] = max(prev, elapsed)
        # Track first error per tool name
        if ctx.tool_timeout is not None:
            self._tool_errors.setdefault(
                ctx.tool_name, f"timeout {ctx.tool_timeout}s"
            )
        elif ctx.tool_error:
            self._tool_errors.setdefault(ctx.tool_name, ctx.tool_error[:80])

    async def after_tools(self, ctx):
        for name, summaries in self._tool_groups:
            merged = merge_summaries(summaries)
            elapsed = self._tool_elapsed.get(name, 0)
            if name in self._tool_errors:
                self._d.tool_fail(name, merged, self._tool_errors[name], elapsed)
            else:
                self._d.tool_done(name, merged, elapsed)

        # Reset spinner for next LLM call
        self._d.spinner_remove("tools_tag")
        self._d.spinner_remove("tools_detail")
        self._d.spinner_set("thinking", "Thinking",
                            priority=Priority.NORMAL, truncate=Truncate.TAIL)
        self._tool_groups = []
        self._tool_errors = {}
        self._tool_call_start = {}
        self._tool_elapsed = {}

    async def on_compact(self, ctx):
        self._d.spinner_remove("thinking")
        self._d.spinner_set("compact", "Compacting",
                            priority=Priority.NORMAL, truncate=Truncate.TAIL)
        self._d.compact(ctx.compact_old, ctx.compact_new)

    # ── Internal ───────────────────────────────────────────

    def _update_spinner_for_tools(self, groups):
        """Update spinner segments to show running tools.

        Format: ``{count} tool(s) · {detail}``
        - detail for single tool: ``read(src/main.py)``
        - detail for multiple:   ``read×2, bash``
        """
        total = sum(len(s) for _, s in groups)
        label = "running 1 tool" if total == 1 else f"running {total} tools"

        parts = []
        for name, summaries in groups:
            count = len(summaries)
            if total == 1:
                # Single tool: show name + merged args
                parts.append(f"{name}({merge_summaries(summaries)})")
            elif count > 1:
                parts.append(f"{name}×{count}")
            else:
                parts.append(name)

        self._d.spinner_remove("thinking")
        self._d.spinner_set("tools_tag", label,
                            priority=Priority.NORMAL, truncate=Truncate.TAIL)
        self._d.spinner_set("tools_detail", ", ".join(parts),
                            priority=Priority.LOW, truncate=Truncate.MIDDLE)

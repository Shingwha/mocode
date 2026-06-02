"""CLIDisplayHook — bridges AgentHook lifecycle to CLI Display."""

from ...core.hook import AgentHook
from .display import _group_tool_calls


class CLIDisplayHook(AgentHook):
    """Renders tool calls, reasoning, and usage to the terminal."""

    def __init__(self, display):
        self._d = display
        self._prompt = self._completion = 0

    async def on_response(self, ctx):
        if ctx.reasoning_content and not (ctx.response and ctx.response.tool_calls):
            self._d.reasoning(ctx.reasoning_content)
        if ctx.final_content and ctx.response and ctx.response.tool_calls:
            self._d.text_response(ctx.final_content)
        if ctx.usage:
            self._prompt += ctx.usage.prompt_tokens
            self._completion += ctx.usage.completion_tokens
        # Batched tool call display
        if ctx.response and ctx.response.tool_calls:
            groups = _group_tool_calls(ctx.response.tool_calls)
            self._d.tool_start_batched(groups)

    async def after_iteration(self, ctx):
        if self._prompt or self._completion:
            self._d.usage(self._prompt, self._completion)
            self._prompt = self._completion = 0

    async def on_tool_start(self, ctx):
        pass  # Already printed batched summaries in on_response

    async def on_tool_complete(self, ctx):
        if ctx.tool_timeout is not None:
            self._d.tool_timeout(ctx.tool_timeout)
        elif ctx.tool_error:
            self._d.tool_error(ctx.tool_error[:80])

    async def on_compact(self, ctx):
        self._d.set_spinner_text("Compacting")
        self._d.compact(ctx.compact_old, ctx.compact_new)

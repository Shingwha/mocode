"""CLIDisplayHook — bridges AgentHook lifecycle to CLI Display."""

from ...core.hook import AgentHook
from .display import _tool_summary


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

    async def after_iteration(self, ctx):
        if self._prompt or self._completion:
            self._d.usage(self._prompt, self._completion)
            self._prompt = self._completion = 0

    async def on_tool_start(self, ctx):
        self._d.tool_start(ctx.tool_name, _tool_summary(ctx.tool_name, ctx.tool_args))

    async def on_tool_complete(self, ctx):
        if ctx.tool_timeout is not None:
            self._d.tool_timeout(ctx.tool_timeout)
        elif ctx.tool_error:
            self._d.tool_error(ctx.tool_error[:80])

    async def on_compact(self, ctx):
        self._d.set_spinner_text("Compacting")
        self._d.compact(ctx.compact_old, ctx.compact_new)

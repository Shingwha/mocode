"""CompactHook — auto-trigger context compression when token usage exceeds threshold."""

from __future__ import annotations

from ..core.hook import AgentHook, IterationContext, CompactContext
from ..core.compact import compact_messages
from ..prompts.compact import summary_system_prompt, COMPACT_USER_TEMPLATE


class CompactHook(AgentHook):
    """Auto-trigger context compression when token usage exceeds threshold."""

    def __init__(
        self,
        agent,
        threshold: float = 0.80,
        context_window: int = 256_000,
    ):
        self._agent = agent
        self._threshold = threshold
        self._context_window = context_window
        self._last_prompt_tokens: int = 0

    @property
    def last_prompt_tokens(self) -> int:
        return self._last_prompt_tokens

    async def before_iteration(self, ctx: IterationContext) -> None:
        if ctx.usage:
            self._last_prompt_tokens = ctx.usage.prompt_tokens
        if self._last_prompt_tokens > self._context_window * self._threshold:
            ctx._needs_compact = True

    async def on_compact(self, ctx: CompactContext) -> None:
        ctx.messages[:] = await compact_messages(
            self._agent.provider,
            ctx.messages,
            summary_system_prompt.build(fmt="xml"),
            COMPACT_USER_TEMPLATE,
        )
        self._last_prompt_tokens = 0

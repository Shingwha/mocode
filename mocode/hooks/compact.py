"""CompactHook — auto-trigger context compression when token usage exceeds threshold."""

from __future__ import annotations

import logging

from ..core.hook import AgentHook, AgentHookContext
from ..tools.compact import compact_messages

logger = logging.getLogger(__name__)


class CompactHook(AgentHook):
    """Auto-trigger context compression when token usage exceeds threshold."""

    def __init__(
        self,
        provider,
        threshold: float = 0.80,
        context_window: int = 128_000,
    ):
        self._provider = provider
        self._threshold = threshold
        self._context_window = context_window
        self._last_prompt_tokens: int = 0

    @property
    def last_prompt_tokens(self) -> int:
        return self._last_prompt_tokens

    async def before_iteration(self, ctx: AgentHookContext) -> None:
        if ctx.usage:
            self._last_prompt_tokens = ctx.usage.prompt_tokens
        if self._last_prompt_tokens > self._context_window * self._threshold:
            old_count = len(ctx.messages)
            ctx.messages[:] = await compact_messages(
                self._provider, ctx.messages,
            )
            ctx.compact_old = old_count
            ctx.compact_new = len(ctx.messages)
            self._last_prompt_tokens = 0
            await self.on_compact(ctx)

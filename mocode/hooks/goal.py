"""GoalHook — keeps loop running when a goal is active."""

from __future__ import annotations

import logging

from ..core.hook import AgentHook, AgentHookContext

logger = logging.getLogger(__name__)


class GoalHook(AgentHook):
    """Keeps the agent loop running while a goal is active.

    The agent self-manages: it calls GoalTool(action="clear") when done.
    This hook just increments the turn counter and sets continue_loop.
    """

    def __init__(self, max_turns: int = 50):
        self._max_turns = max_turns
        self._condition: str | None = None
        self._turn_count: int = 0

    @property
    def condition(self) -> str | None:
        return self._condition

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def set_goal(self, condition: str) -> None:
        self._condition = condition
        self._turn_count = 0

    def clear_goal(self) -> None:
        self._condition = None
        self._turn_count = 0

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        if not self._condition:
            return

        self._turn_count += 1
        if self._turn_count > self._max_turns:
            logger.warning(f"Goal exceeded max turns ({self._max_turns}), stopping")
            self._condition = None
            ctx.continue_loop = False
            return

        ctx.continue_loop = True

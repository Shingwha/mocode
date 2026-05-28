"""GoalHook — keeps loop running when a goal is active."""

from __future__ import annotations

import logging

from ..core.hook import AgentHook, AgentHookContext

logger = logging.getLogger(__name__)


class GoalHook(AgentHook):
    """Keeps the agent loop running while a goal is active.

    The agent self-manages: it calls GoalTool(action="clear") when done.
    When max_turns is reached, the goal is paused (not destroyed) so the
    LLM or user can resume it later.
    """

    def __init__(self, max_turns: int = 300):
        self._max_turns = max_turns
        self._condition: str | None = None
        self._paused: bool = False
        self._turn_count: int = 0

    @property
    def condition(self) -> str | None:
        return self._condition

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def max_turns(self) -> int:
        return self._max_turns

    def set_goal(self, condition: str) -> None:
        self._condition = condition
        self._paused = False
        self._turn_count = 0

    def pause_goal(self) -> None:
        self._paused = True

    def resume_goal(self) -> None:
        self._paused = False
        self._turn_count = 0

    def clear_goal(self) -> None:
        self._condition = None
        self._paused = False
        self._turn_count = 0

    async def after_tools(self, ctx: AgentHookContext) -> None:
        await self._tick(ctx)

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        await self._tick(ctx)

    async def _tick(self, ctx: AgentHookContext) -> None:
        if not self._condition or self._paused:
            return

        self._turn_count += 1
        if self._turn_count > self._max_turns:
            logger.warning(f"Goal hit max turns ({self._max_turns}), pausing")
            self._paused = True
            ctx.messages.append({
                "role": "user",
                "content": (
                    f"[Goal] Turn limit ({self._max_turns}) reached, goal paused.\n"
                    f"Goal: {self._condition}\n"
                    f"Use goal(action=\"resume\") to continue, or goal(action=\"clear\") to stop."
                ),
            })
            ctx.continue_loop = False
            return

        ctx.continue_loop = True

"""Tests for goal — GoalHook and GoalTool."""

import pytest

from mocode.core import AgentHookContext
from mocode.core.tool import ToolError
from mocode.tools.goal import GoalTool
from mocode.hooks.goal import GoalHook


# ---- GoalHook ----


class TestGoalHook:
    @pytest.mark.asyncio
    async def test_no_goal_is_noop(self):
        hook = GoalHook()
        ctx = AgentHookContext(messages=[{"role": "user", "content": "hi"}])
        await hook.after_iteration(ctx)
        assert not ctx.continue_loop
        assert len(ctx.messages) == 1

    @pytest.mark.asyncio
    async def test_continues_loop_when_goal_active(self):
        hook = GoalHook()
        hook.set_goal("implement feature X")

        ctx = AgentHookContext(
            messages=[
                {"role": "user", "content": "do stuff"},
                {"role": "assistant", "content": "working on it"},
            ]
        )
        await hook.after_iteration(ctx)

        assert ctx.continue_loop is True
        assert len(ctx.messages) == 3
        assert "[Goal]" in ctx.messages[-1]["content"]

    def test_set_and_clear_goal(self):
        hook = GoalHook()

        assert hook.condition is None
        hook.set_goal("do something")
        assert hook.condition == "do something"
        assert hook.turn_count == 0
        hook.clear_goal()
        assert hook.condition is None


# ---- GoalTool ----


class TestGoalTool:
    @pytest.mark.asyncio
    async def test_set_action(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "set", "goal": "fix all bugs"})
        assert "fix all bugs" in result
        assert hook.condition == "fix all bugs"

    @pytest.mark.asyncio
    async def test_clear_action(self):
        hook = GoalHook()
        hook.set_goal("something")
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "clear"})
        assert "something" in result
        assert hook.condition is None

    @pytest.mark.asyncio
    async def test_status_with_active_goal(self):
        hook = GoalHook()
        hook.set_goal("complete the task")

        tool = GoalTool(hook)
        result = await tool.run_async({"action": "status"})
        assert "complete the task" in result
        assert "active" in result

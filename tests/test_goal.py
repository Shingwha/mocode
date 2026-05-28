"""Tests for goal — GoalHook and GoalTool."""

import pytest

from mocode.core import AgentHookContext
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

        ctx = AgentHookContext(messages=[
            {"role": "user", "content": "do stuff"},
            {"role": "assistant", "content": "working on it"},
        ])
        await hook.after_iteration(ctx)

        assert ctx.continue_loop is True
        assert len(ctx.messages) == 2

    @pytest.mark.asyncio
    async def test_max_turns_safety(self):
        hook = GoalHook(max_turns=2)
        hook.set_goal("impossible goal")

        ctx = AgentHookContext(messages=[{"role": "user", "content": "start"}])

        # Turn 1
        await hook.after_iteration(ctx)
        assert ctx.continue_loop is True

        # Turn 2
        ctx.continue_loop = False
        await hook.after_iteration(ctx)
        assert ctx.continue_loop is True

        # Turn 3 — exceeds max
        ctx.continue_loop = False
        await hook.after_iteration(ctx)
        assert ctx.continue_loop is False
        assert hook.condition is None

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
    def test_schema_has_action_and_goal(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        schema = tool.to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "action" in props
        assert "goal" in props
        assert props["action"]["enum"] == ["set", "status", "clear"]

    @pytest.mark.asyncio
    async def test_set_action(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "set", "goal": "fix all bugs"})
        assert "fix all bugs" in result
        assert hook.condition == "fix all bugs"

    @pytest.mark.asyncio
    async def test_set_without_goal_returns_error(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "set"})
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_clear_action(self):
        hook = GoalHook()
        hook.set_goal("something")
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "clear"})
        assert "something" in result
        assert hook.condition is None

    @pytest.mark.asyncio
    async def test_clear_no_active_goal(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "clear"})
        assert "No active goal" in result

    @pytest.mark.asyncio
    async def test_status_no_active_goal(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "status"})
        assert "No active goal" in result

    @pytest.mark.asyncio
    async def test_status_with_active_goal(self):
        hook = GoalHook()
        hook.set_goal("complete the task")

        tool = GoalTool(hook)
        result = await tool.run_async({"action": "status"})
        assert "complete the task" in result

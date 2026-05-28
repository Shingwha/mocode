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
    async def test_max_turns_pauses_goal(self):
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

        # Turn 3 — exceeds max, pauses instead of clearing
        ctx.continue_loop = False
        await hook.after_iteration(ctx)
        assert ctx.continue_loop is False
        assert hook.condition == "impossible goal"
        assert hook.paused is True
        # Injected user message about pausing
        assert any("[Goal]" in m.get("content", "") for m in ctx.messages)

    @pytest.mark.asyncio
    async def test_paused_goal_stops_loop(self):
        hook = GoalHook()
        hook.set_goal("something")
        hook.pause_goal()

        ctx = AgentHookContext(messages=[{"role": "user", "content": "hi"}])
        await hook.after_iteration(ctx)
        assert ctx.continue_loop is False
        assert hook.turn_count == 0

    @pytest.mark.asyncio
    async def test_resumed_goal_continues_loop(self):
        hook = GoalHook(max_turns=5)
        hook.set_goal("something")
        # Simulate reaching max turns
        hook._turn_count = 5
        hook.pause_goal()
        hook.resume_goal()

        assert hook.turn_count == 0
        ctx = AgentHookContext(messages=[{"role": "user", "content": "hi"}])
        await hook.after_iteration(ctx)
        assert ctx.continue_loop is True
        assert hook.turn_count == 1

    @pytest.mark.asyncio
    async def test_after_tools_ticks(self):
        hook = GoalHook()
        hook.set_goal("something")

        ctx = AgentHookContext(messages=[{"role": "user", "content": "hi"}])
        await hook.after_tools(ctx)
        assert ctx.continue_loop is True
        assert hook.turn_count == 1

    def test_set_and_clear_goal(self):
        hook = GoalHook()

        assert hook.condition is None
        hook.set_goal("do something")
        assert hook.condition == "do something"
        assert hook.turn_count == 0
        hook.clear_goal()
        assert hook.condition is None

    def test_set_resets_pause(self):
        hook = GoalHook()
        hook.set_goal("old")
        hook.pause_goal()
        assert hook.paused is True
        hook.set_goal("new")
        assert hook.paused is False
        assert hook.condition == "new"


# ---- GoalTool ----


class TestGoalTool:
    def test_schema_has_action_and_goal(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        schema = tool.to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "action" in props
        assert "goal" in props
        assert props["action"]["enum"] == ["set", "pause", "resume", "status", "clear"]

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
    async def test_pause_action(self):
        hook = GoalHook()
        hook.set_goal("something")
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "pause"})
        assert "paused" in result.lower()
        assert hook.paused is True

    @pytest.mark.asyncio
    async def test_pause_no_goal(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "pause"})
        assert "No active goal" in result

    @pytest.mark.asyncio
    async def test_pause_already_paused(self):
        hook = GoalHook()
        hook.set_goal("something")
        hook.pause_goal()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "pause"})
        assert "already paused" in result.lower()

    @pytest.mark.asyncio
    async def test_resume_action(self):
        hook = GoalHook()
        hook.set_goal("something")
        hook._turn_count = 100
        hook.pause_goal()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "resume"})
        assert "resumed" in result.lower()
        assert hook.paused is False
        assert hook.turn_count == 0

    @pytest.mark.asyncio
    async def test_resume_not_paused(self):
        hook = GoalHook()
        hook.set_goal("something")
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "resume"})
        assert "not paused" in result.lower()

    @pytest.mark.asyncio
    async def test_resume_no_goal(self):
        hook = GoalHook()
        tool = GoalTool(hook)

        result = await tool.run_async({"action": "resume"})
        assert "No goal" in result

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
        assert "active" in result

    @pytest.mark.asyncio
    async def test_status_shows_paused(self):
        hook = GoalHook()
        hook.set_goal("something")
        hook.pause_goal()

        tool = GoalTool(hook)
        result = await tool.run_async({"action": "status"})
        assert "paused" in result

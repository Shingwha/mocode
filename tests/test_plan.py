"""Tests for plan system — PlanTool and /plan commands."""

import pytest
from unittest.mock import MagicMock

from mocode.core.tool import ToolError
from mocode.tools.plan import PlanState, PlanTool
from mocode.app.cli.commands import CommandContext, CommandResult
from mocode.app.cli.commands.plan import (
    command as plan_command,
    _start,
    _start_clean,
)


def _make_ctx(app=None, display=None, args=""):
    return CommandContext(
        app=app or MagicMock(),
        args=args,
        display=display or MagicMock(),
    )


def _app_with_plan(path=None):
    """Create a mock app with a PlanState."""
    app = MagicMock()
    app.plan_state = PlanState(active_plan_path=path)
    return app


# ---- PlanTool ----


class TestPlanTool:
    @pytest.mark.asyncio
    async def test_done_sets_path(self):
        ps = PlanState()
        tool = PlanTool(ps)
        result = await tool.run_async({"action": "done", "path": "~/.mocode/plans/test.md"})
        assert ps.active_plan_path == "~/.mocode/plans/test.md"
        assert "registered" in result.lower()

    @pytest.mark.asyncio
    async def test_done_requires_path(self):
        tool = PlanTool(PlanState())
        with pytest.raises(ToolError):
            await tool.run_async({"action": "done"})

    @pytest.mark.asyncio
    async def test_status_no_plan(self):
        tool = PlanTool(PlanState())
        result = await tool.run_async({"action": "status"})
        assert "no active plan" in result.lower()


# ---- /plan command (via Command instance) ----


class TestPlanCommand:
    """Test /plan command via the Command dataclass."""

    @pytest.mark.asyncio
    async def test_start_no_plan_warns(self):
        app = _app_with_plan()
        display = MagicMock()
        result = await _start(_make_ctx(app=app, display=display), "")
        assert result == CommandResult.CONTINUE
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_returns_prompt(self):
        app = _app_with_plan("~/.mocode/plans/test.md")
        result = await _start(_make_ctx(app=app), "")
        assert result.kind == "prompt"
        assert "test.md" in result.prompt

    @pytest.mark.asyncio
    async def test_start_clean_clears_conversation(self):
        app = _app_with_plan("~/.mocode/plans/test.md")
        result = await _start_clean(_make_ctx(app=app), "")
        app.clear_conversation.assert_called_once()
        assert result.kind == "prompt"


# ---- /plan command routing via Command.run() ----


class TestPlanCommandRouting:
    """Test that the Command dataclass routes subcommands correctly."""

    @pytest.mark.asyncio
    async def test_subcommand_routing_start(self):
        app = _app_with_plan("~/.mocode/plans/test.md")
        ctx = _make_ctx(app=app, args="start")
        result = await plan_command.run(ctx)
        assert result.kind == "prompt"
        assert "test.md" in result.prompt

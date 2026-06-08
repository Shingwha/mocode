"""Tests for plan system — PlanTool and /plan commands."""

import pytest
from unittest.mock import MagicMock

from mocode.core.tool import ToolError
from mocode.tools.plan import PlanTool
from mocode.app.cli.commands import CommandContext, CommandResult
from mocode.app.cli.commands.plan import (
    command as plan_command,
    _start,
    _start_clean,
    _clear,
)


def _make_ctx(app=None, display=None, args=""):
    return CommandContext(
        app=app or MagicMock(),
        args=args,
        display=display or MagicMock(),
    )


# ---- PlanTool ----


class TestPlanTool:
    @pytest.mark.asyncio
    async def test_done_sets_path(self):
        app = MagicMock()
        app.active_plan_path = None
        tool = PlanTool(app)
        result = await tool.run_async({"action": "done", "path": "~/.mocode/plans/test.md"})
        assert app.active_plan_path == "~/.mocode/plans/test.md"
        assert "registered" in result.lower()

    @pytest.mark.asyncio
    async def test_done_requires_path(self):
        tool = PlanTool(MagicMock())
        with pytest.raises(ToolError):
            await tool.run_async({"action": "done"})

    @pytest.mark.asyncio
    async def test_status_no_plan(self):
        app = MagicMock()
        app.active_plan_path = None
        tool = PlanTool(app)
        result = await tool.run_async({"action": "status"})
        assert "no active plan" in result.lower()


# ---- /plan command (via Command instance) ----


class TestPlanCommand:
    """Test /plan command via the Command dataclass."""

    @pytest.mark.asyncio
    async def test_start_no_plan_warns(self):
        app = MagicMock()
        app.active_plan_path = None
        display = MagicMock()
        result = await _start(_make_ctx(app=app, display=display), "")
        assert result == CommandResult.CONTINUE
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_returns_prompt(self):
        app = MagicMock()
        app.active_plan_path = "~/.mocode/plans/test.md"
        result = await _start(_make_ctx(app=app), "")
        assert result.kind == "prompt"
        assert "test.md" in result.prompt

    @pytest.mark.asyncio
    async def test_start_clean_clears_conversation(self):
        app = MagicMock()
        app.active_plan_path = "~/.mocode/plans/test.md"
        result = await _start_clean(_make_ctx(app=app), "")
        app.clear_conversation.assert_called_once()
        assert result.kind == "prompt"

    @pytest.mark.asyncio
    async def test_clear_resets_path(self):
        app = MagicMock()
        app.active_plan_path = "test.md"
        result = await _clear(_make_ctx(app=app), "")
        assert app.active_plan_path is None
        assert result == CommandResult.CONTINUE


# ---- /plan command routing via Command.run() ----


class TestPlanCommandRouting:
    """Test that the Command dataclass routes subcommands correctly."""

    @pytest.mark.asyncio
    async def test_subcommand_routing_start(self):
        app = MagicMock()
        app.active_plan_path = "~/.mocode/plans/test.md"
        ctx = _make_ctx(app=app, args="start")
        result = await plan_command.run(ctx)
        assert result.kind == "prompt"
        assert "test.md" in result.prompt

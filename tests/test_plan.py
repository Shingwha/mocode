"""Tests for plan system — PlanTool and /plan:* commands."""

import pytest
from unittest.mock import MagicMock

from mocode.core.tool import ToolError
from mocode.tools.plan import PlanTool
from mocode.app.cli.commands import CommandContext, CommandResult
from mocode.app.cli.commands.plan import (
    _PlanHandler,
    PlanStartCommand,
    PlanStartCleanCommand,
    PlanStatusCommand,
    PlanClearCommand,
    PlanCopyCommand,
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

    @pytest.mark.asyncio
    async def test_status_shows_path(self):
        app = MagicMock()
        app.active_plan_path = "test.md"
        tool = PlanTool(app)
        result = await tool.run_async({"action": "status"})
        assert "test.md" in result

    @pytest.mark.asyncio
    async def test_clear_resets_path(self):
        app = MagicMock()
        app.active_plan_path = "test.md"
        tool = PlanTool(app)
        result = await tool.run_async({"action": "clear"})
        assert app.active_plan_path is None
        assert "cleared" in result.lower()

    @pytest.mark.asyncio
    async def test_unknown_action_raises(self):
        tool = PlanTool(MagicMock())
        with pytest.raises(ToolError):
            await tool.run_async({"action": "invalid"})


# ---- /plan:* commands ----


class TestPlanCommands:
    @pytest.mark.asyncio
    async def test_start_no_plan_warns(self):
        app = MagicMock()
        app.active_plan_path = None
        display = MagicMock()
        handler = _PlanHandler(app)
        cmd = PlanStartCommand(handler)
        result = await cmd.run(_make_ctx(app=app, display=display))
        assert result == CommandResult.CONTINUE
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_returns_prompt(self):
        app = MagicMock()
        app.active_plan_path = "~/.mocode/plans/test.md"
        handler = _PlanHandler(app)
        cmd = PlanStartCommand(handler)
        result = await cmd.run(_make_ctx(app=app))
        assert result.kind == "prompt"
        assert "test.md" in result.prompt

    @pytest.mark.asyncio
    async def test_start_with_context(self):
        app = MagicMock()
        app.active_plan_path = "~/.mocode/plans/test.md"
        handler = _PlanHandler(app)
        cmd = PlanStartCommand(handler)
        result = await cmd.run(_make_ctx(app=app, args="use fast mode"))
        assert "use fast mode" in result.prompt

    @pytest.mark.asyncio
    async def test_start_clean_clears_conversation(self):
        app = MagicMock()
        app.active_plan_path = "~/.mocode/plans/test.md"
        handler = _PlanHandler(app)
        cmd = PlanStartCleanCommand(handler)
        result = await cmd.run(_make_ctx(app=app))
        app.clear_conversation.assert_called_once()
        assert result.kind == "prompt"

    @pytest.mark.asyncio
    async def test_start_clean_no_plan_warns(self):
        app = MagicMock()
        app.active_plan_path = None
        display = MagicMock()
        handler = _PlanHandler(app)
        cmd = PlanStartCleanCommand(handler)
        result = await cmd.run(_make_ctx(app=app, display=display))
        assert result == CommandResult.CONTINUE
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_status_shows_path(self):
        app = MagicMock()
        app.active_plan_path = "test.md"
        display = MagicMock()
        handler = _PlanHandler(app)
        cmd = PlanStatusCommand(handler)
        result = await cmd.run(_make_ctx(app=app, display=display))
        assert result == CommandResult.CONTINUE
        assert "test.md" in display.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_no_plan(self):
        app = MagicMock()
        app.active_plan_path = None
        display = MagicMock()
        handler = _PlanHandler(app)
        cmd = PlanStatusCommand(handler)
        result = await cmd.run(_make_ctx(app=app, display=display))
        assert result == CommandResult.CONTINUE
        assert "no active plan" in display.info.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_clear_resets_path(self):
        app = MagicMock()
        app.active_plan_path = "test.md"
        handler = _PlanHandler(app)
        cmd = PlanClearCommand(handler)
        result = await cmd.run(_make_ctx(app=app))
        assert app.active_plan_path is None
        assert result == CommandResult.CONTINUE

    @pytest.mark.asyncio
    async def test_copy_no_plan_warns(self):
        app = MagicMock()
        app.active_plan_path = None
        display = MagicMock()
        handler = _PlanHandler(app)
        cmd = PlanCopyCommand(handler)
        result = await cmd.run(_make_ctx(app=app, display=display))
        assert result == CommandResult.CONTINUE
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_copy_reads_and_copies(self, tmp_path):
        pyperclip = pytest.importorskip("pyperclip")

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# My Plan\n\nDo stuff.", encoding="utf-8")

        app = MagicMock()
        app.active_plan_path = str(plan_file)
        display = MagicMock()
        handler = _PlanHandler(app)
        cmd = PlanCopyCommand(handler)
        result = await cmd.run(_make_ctx(app=app, display=display))
        assert result == CommandResult.CONTINUE
        assert pyperclip.paste() == "# My Plan\n\nDo stuff."
        display.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_copy_file_not_found(self, tmp_path):
        app = MagicMock()
        app.active_plan_path = str(tmp_path / "nonexistent.md")
        display = MagicMock()
        handler = _PlanHandler(app)
        cmd = PlanCopyCommand(handler)
        result = await cmd.run(_make_ctx(app=app, display=display))
        assert result == CommandResult.CONTINUE
        display.error.assert_called_once()

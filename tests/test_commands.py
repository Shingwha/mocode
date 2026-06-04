"""Tests for CommandRegistry, CommandResult, and individual commands."""

import json
from unittest.mock import MagicMock

import pytest

from mocode.app.cli.commands import CommandContext, CommandRegistry, CommandResult
from mocode.app.cli.commands.quit import QuitCommand
from mocode.app.cli.commands.help import HelpCommand
from mocode.app.cli.commands.export import ExportCommand
from mocode.app.cli.commands.clear import ClearCommand
from mocode.app.cli.commands.resume import ResumeCommand


def _make_ctx(app=None, display=None, args=""):
    return CommandContext(
        app=app or MagicMock(),
        args=args,
        display=display or MagicMock(),
    )


class TestCommandRegistry:
    def test_register_and_get_by_name(self):
        reg = CommandRegistry()
        cmd = QuitCommand()
        reg.register(cmd)
        assert reg.get("/quit") is cmd

    def test_get_by_alias(self):
        reg = CommandRegistry()
        cmd = QuitCommand()
        reg.register(cmd)
        assert reg.get("/exit") is cmd
        assert reg.get("quit") is cmd
        assert reg.get("exit") is cmd

    def test_get_unknown_returns_none(self):
        reg = CommandRegistry()
        assert reg.get("/nonexistent") is None


class TestQuitCommand:
    @pytest.mark.asyncio
    async def test_returns_exit(self):
        cmd = QuitCommand()
        result = await cmd.run(_make_ctx())
        assert result == CommandResult.EXIT


class TestClearCommand:
    @pytest.mark.asyncio
    async def test_calls_replace_messages(self):
        app = MagicMock()
        cmd = ClearCommand()
        result = await cmd.run(_make_ctx(app=app))
        assert result == CommandResult.CONTINUE
        app.replace_messages.assert_called_once_with([])


class TestExportCommand:
    @pytest.mark.asyncio
    async def test_exports_messages(self, tmp_path, monkeypatch):
        app = MagicMock()
        app.agent.messages = [{"role": "user", "content": "hi"}]
        app.agent.system_prompt = "You are helpful."
        display = MagicMock()

        monkeypatch.chdir(tmp_path)
        cmd = ExportCommand()
        result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        display.info.assert_called_once()
        info_msg = display.info.call_args[0][0]
        assert "Exported 1 msgs" in info_msg
        assert "prompt 16 chars" in info_msg

        # Verify the file was created with correct structure
        files = list(tmp_path.glob("session_*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["system_prompt"] == "You are helpful."
        assert data["messages"] == [{"role": "user", "content": "hi"}]


class TestResumeCommand:
    @pytest.mark.asyncio
    async def test_resume_from_exported_file(self, tmp_path):
        # Write an export-style file
        export_data = {
            "system_prompt": "You are a coder.",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        }
        path = tmp_path / "session_test.json"
        path.write_text(
            json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        app = MagicMock()
        display = MagicMock()
        cmd = ResumeCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args=str(path)))

        assert result == CommandResult.CONTINUE
        app.replace_messages.assert_called_once_with(export_data["messages"])

    @pytest.mark.asyncio
    async def test_resume_rejects_invalid_format(self, tmp_path):
        # Old array format — should fail
        path = tmp_path / "old.json"
        path.write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")

        app = MagicMock()
        display = MagicMock()
        cmd = ResumeCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args=str(path)))

        assert result == CommandResult.CONTINUE
        app.replace_messages.assert_not_called()
        display.error.assert_called_once()
        assert "Invalid format" in display.error.call_args[0][0]

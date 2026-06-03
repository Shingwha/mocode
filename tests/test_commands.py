"""Tests for CommandRegistry, CommandResult, and individual commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.app.cli.commands import CommandContext, CommandRegistry, CommandResult
from mocode.app.cli.commands.quit import QuitCommand
from mocode.app.cli.commands.help import HelpCommand
from mocode.app.cli.commands.export import ExportCommand
from mocode.app.cli.commands.clear import ClearCommand


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

    def test_all_returns_in_order(self):
        reg = CommandRegistry()
        q = QuitCommand()
        h = HelpCommand()
        reg.register(q)
        reg.register(h)
        assert reg.all() == [q, h]


class TestQuitCommand:
    @pytest.mark.asyncio
    async def test_returns_exit(self):
        cmd = QuitCommand()
        result = await cmd.run(_make_ctx())
        assert result == CommandResult.EXIT


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_lists_commands(self):
        reg = CommandRegistry()
        reg.register(QuitCommand())
        reg.register(HelpCommand())

        app = MagicMock()
        app.commands = reg
        display = MagicMock()

        cmd = HelpCommand()
        result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        display.info.assert_called_once()
        msg = display.info.call_args[0][0]
        assert "/quit" in msg
        assert "/help" in msg

    @pytest.mark.asyncio
    async def test_empty_registry(self):
        app = MagicMock()
        app.commands = CommandRegistry()
        display = MagicMock()

        cmd = HelpCommand()
        result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        display.info.assert_called_once()


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
        display = MagicMock()

        monkeypatch.chdir(tmp_path)
        cmd = ExportCommand()
        result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        display.info.assert_called_once()
        info_msg = display.info.call_args[0][0]
        assert "Exported 1 msgs" in info_msg

        # Verify the file was created
        files = list(tmp_path.glob("session_*.json"))
        assert len(files) == 1

class TestCopyCommand:
    @pytest.mark.asyncio
    async def test_copies_last_assistant_response(self):
        app = MagicMock()
        app.agent.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "first response"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "target response"},
        ]
        display = MagicMock()

        from mocode.app.cli.commands.copy import CopyCommand

        with patch("pyperclip.copy") as mock_copy:
            cmd = CopyCommand()
            result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        mock_copy.assert_called_once_with("target response")
        display.info.assert_called_once()
        assert "Copied: target response" in display.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_skips_tool_call_messages(self):
        app = MagicMock()
        app.agent.messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "content": "result"},
            {"role": "assistant", "content": "the real response"},
        ]
        display = MagicMock()

        from mocode.app.cli.commands.copy import CopyCommand

        with patch("pyperclip.copy") as mock_copy:
            cmd = CopyCommand()
            result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        mock_copy.assert_called_once_with("the real response")

    @pytest.mark.asyncio
    async def test_warns_when_no_assistant_response(self):
        app = MagicMock()
        app.agent.messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "result"},
        ]
        display = MagicMock()

        from mocode.app.cli.commands.copy import CopyCommand

        with patch("pyperclip.copy") as mock_copy:
            cmd = CopyCommand()
            result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        mock_copy.assert_not_called()
        display.warn.assert_called_once()
        assert "No assistant response" in display.warn.call_args[0][0]

    @pytest.mark.asyncio
    async def test_warns_on_empty_messages(self):
        app = MagicMock()
        app.agent.messages = []
        display = MagicMock()

        from mocode.app.cli.commands.copy import CopyCommand

        with patch("pyperclip.copy") as mock_copy:
            cmd = CopyCommand()
            result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        mock_copy.assert_not_called()
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_clipboard_error_gracefully(self):
        app = MagicMock()
        app.agent.messages = [
            {"role": "assistant", "content": "some content"},
        ]
        display = MagicMock()

        from mocode.app.cli.commands.copy import CopyCommand

        with patch("pyperclip.copy", side_effect=RuntimeError("no DISPLAY")):
            cmd = CopyCommand()
            result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        display.error.assert_called_once()
        assert "Clipboard error" in display.error.call_args[0][0]

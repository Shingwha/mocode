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
from mocode.app.cli.commands.skill import make_skill_command
from mocode.app.session import Session


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
    async def test_calls_clear_conversation(self):
        app = MagicMock()
        cmd = ClearCommand()
        result = await cmd.run(_make_ctx(app=app))
        assert result == CommandResult.CONTINUE
        app.clear_conversation.assert_called_once()


class TestExportCommand:
    @pytest.mark.asyncio
    async def test_exports_messages(self, tmp_path, monkeypatch):
        session = Session(
            id="session_test",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hi"}],
        )
        app = MagicMock()
        app.session_mgr.get_active.return_value = session
        app.agent.system_prompt = "You are helpful."
        display = MagicMock()

        monkeypatch.chdir(tmp_path)
        cmd = ExportCommand()
        result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        display.info.assert_called_once()
        info_msg = display.info.call_args[0][0]
        assert "Exported 1 msgs" in info_msg

        app.session_mgr.export_to_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_warns_when_no_session(self):
        app = MagicMock()
        app.session_mgr.get_active.return_value = None
        display = MagicMock()

        cmd = ExportCommand()
        result = await cmd.run(_make_ctx(app=app, display=display))

        assert result == CommandResult.CONTINUE
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_export_md_format(self, tmp_path, monkeypatch):
        session = Session(
            id="session_test",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hi"}],
        )
        app = MagicMock()
        app.session_mgr.get_active.return_value = session
        app.agent.system_prompt = "You are helpful."
        display = MagicMock()

        monkeypatch.chdir(tmp_path)
        cmd = ExportCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="md"))

        assert result == CommandResult.CONTINUE
        app.session_mgr.export_to_md.assert_called_once()
        call_args = app.session_mgr.export_to_md.call_args
        assert call_args[0][1].suffix == ".md"
        display.info.assert_called_once()
        assert ".md" in display.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_export_json_explicit(self, tmp_path, monkeypatch):
        session = Session(
            id="session_test",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hi"}],
        )
        app = MagicMock()
        app.session_mgr.get_active.return_value = session
        app.agent.system_prompt = "You are helpful."
        display = MagicMock()

        monkeypatch.chdir(tmp_path)
        cmd = ExportCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="json"))

        assert result == CommandResult.CONTINUE
        app.session_mgr.export_to_file.assert_called_once()
        call_args = app.session_mgr.export_to_file.call_args
        assert call_args[0][1].suffix == ".json"


class TestResumeCommand:
    @pytest.mark.asyncio
    async def test_resume_from_exported_file(self, tmp_path):
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
        app.resume_from_file.assert_called_once_with(export_data["messages"])

    @pytest.mark.asyncio
    async def test_resume_rejects_invalid_format(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")

        app = MagicMock()
        display = MagicMock()
        cmd = ResumeCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args=str(path)))

        assert result == CommandResult.CONTINUE
        app.resume_from_file.assert_not_called()
        display.warn.assert_called_once()


class TestSkillCommand:
    @pytest.mark.asyncio
    async def test_basic_load(self):
        skill = MagicMock()
        skill.metadata.name = "workflow"
        skill.metadata.description = "DAG orchestration"
        skill.load_content.return_value = "instructions here"
        cmd = make_skill_command(skill)
        assert cmd.name == "/skill:workflow"
        assert cmd.description == "DAG orchestration"
        result = await cmd.run(_make_ctx())
        assert result.kind == "prompt"
        assert "[Skill:workflow" in result.prompt
        assert "do NOT call the skill tool" in result.prompt
        assert "instructions here" in result.prompt
        assert "User request:" not in result.prompt

    @pytest.mark.asyncio
    async def test_with_user_request(self):
        skill = MagicMock()
        skill.metadata.name = "kami"
        skill.metadata.description = "PDF typesetting"
        skill.load_content.return_value = "typeset instructions"
        cmd = make_skill_command(skill)
        result = await cmd.run(_make_ctx(args="帮我做一份简历"))
        assert "User request: 帮我做一份简历" in result.prompt
        assert "typeset instructions" in result.prompt
        assert "do NOT call the skill tool" in result.prompt

    @pytest.mark.asyncio
    async def test_empty_content_warns(self):
        skill = MagicMock()
        skill.metadata.name = "empty"
        skill.metadata.description = "Empty skill"
        skill.load_content.return_value = ""
        cmd = make_skill_command(skill)
        display = MagicMock()
        result = await cmd.run(_make_ctx(display=display))
        assert result == CommandResult.CONTINUE
        display.warn.assert_called_once()

    def test_registered_in_registry(self):
        """Verify skill commands can be registered and looked up."""
        skill = MagicMock()
        skill.metadata.name = "test-skill"
        skill.metadata.description = "A test"
        skill.load_content.return_value = "body"
        cmd = make_skill_command(skill)
        reg = CommandRegistry()
        reg.register(cmd)
        assert reg.get("/skill:test-skill") is cmd

    def test_no_aliases(self):
        skill = MagicMock()
        skill.metadata.name = "foo"
        skill.metadata.description = ""
        skill.load_content.return_value = "x"
        cmd = make_skill_command(skill)
        assert cmd.aliases == ()

"""Tests for CommandRegistry, Command handling, and the built-in commands."""

import json
from unittest.mock import MagicMock

import pytest

from mocode.app.cli.commands import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from mocode.app.cli.commands import misc, model, session as session_cmds
from mocode.app.plugin.builtin.skills import make_skill_command
from mocode.app.session import Session

BUILTIN_COMMANDS = (*misc.commands, *model.commands, *session_cmds.commands)


_UNSET = object()


def _make_ctx(app=None, display=_UNSET, args=""):
    """Build a CommandContext; ``display=None`` means non-interactive."""
    return CommandContext(
        app=app or MagicMock(),
        args=args,
        display=MagicMock() if display is _UNSET else display,
    )


def _get_builtin_cmd(name: str) -> Command:
    for cmd in BUILTIN_COMMANDS:
        if cmd.name == name:
            return cmd
    raise ValueError(f"Builtin command '{name}' not found")


class TestCommandRegistry:
    def test_register_and_get_by_name(self):
        reg = CommandRegistry()
        cmd = _get_builtin_cmd("/quit")
        reg.register(cmd)
        assert reg.get("/quit") is cmd

    def test_get_by_alias(self):
        reg = CommandRegistry()
        reg.register(_get_builtin_cmd("/quit"))
        assert reg.get("/exit") is not None
        assert reg.get("quit") is not None
        assert reg.get("exit") is not None

    def test_get_unknown_returns_none(self):
        assert CommandRegistry().get("/nonexistent") is None

    def test_register_multiple_and_all_is_sorted(self):
        reg = CommandRegistry()
        reg.register(*BUILTIN_COMMANDS)
        names = [c.name for c in reg.all()]
        assert names == sorted(names)
        assert "/help" in names


class TestCommandResults:
    def test_text_builds_prompt_result(self):
        result = CommandResult.text("hi")
        assert result.kind is Kind.PROMPT
        assert result.prompt == "hi"

    def test_sentinels(self):
        assert CONTINUE.kind is Kind.CONTINUE
        assert EXIT.kind is Kind.EXIT
        assert CONTINUE.prompt is None


class TestQuitCommand:
    @pytest.mark.asyncio
    async def test_returns_exit(self):
        result = await _get_builtin_cmd("/quit").handler(_make_ctx())
        assert result is EXIT


class TestClearCommand:
    @pytest.mark.asyncio
    async def test_calls_clear_conversation(self):
        app = MagicMock()
        result = await _get_builtin_cmd("/clear").handler(_make_ctx(app=app))
        assert result is CONTINUE
        app.clear_conversation.assert_called_once()


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_lists_registered_commands(self):
        app = MagicMock()
        app.commands.all.return_value = [_get_builtin_cmd("/quit"), _get_builtin_cmd("/help")]
        display = MagicMock()
        await _get_builtin_cmd("/help").handler(_make_ctx(app=app, display=display))
        assert "/quit" in display.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_non_interactive_is_a_noop(self):
        app = MagicMock()
        result = await _get_builtin_cmd("/help").handler(_make_ctx(app=app, display=None))
        assert result is CONTINUE


class TestExportCommand:
    @pytest.mark.asyncio
    async def test_exports_json_by_default(self, tmp_path, monkeypatch):
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
        result = await _get_builtin_cmd("/export").handler(_make_ctx(app=app, display=display))

        assert result is CONTINUE
        app.session_mgr.export_to_file.assert_called_once()
        assert "Exported 1 msgs" in display.info.call_args[0][0]

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

        monkeypatch.chdir(tmp_path)
        await _get_builtin_cmd("/export").handler(_make_ctx(app=app, args="md"))

        app.session_mgr.export_to_md.assert_called_once()
        assert app.session_mgr.export_to_md.call_args[0][1].suffix == ".md"


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
        path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")

        app = MagicMock()
        result = await _get_builtin_cmd("/resume").handler(
            _make_ctx(app=app, args=str(path))
        )

        assert result is CONTINUE
        app.resume_from_file.assert_called_once_with(export_data["messages"])

    @pytest.mark.asyncio
    async def test_resume_rejects_invalid_format(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")

        app = MagicMock()
        display = MagicMock()
        await _get_builtin_cmd("/resume").handler(
            _make_ctx(app=app, display=display, args=str(path))
        )

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

        result = await cmd.handler(_make_ctx())
        assert result.kind is Kind.PROMPT
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

        result = await make_skill_command(skill).handler(_make_ctx(args="帮我做一份简历"))
        assert "User request: 帮我做一份简历" in result.prompt
        assert "typeset instructions" in result.prompt

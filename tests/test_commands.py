"""CommandRegistry, CommandResult, and the terminal's built-in commands.

The command *contract* lives in the host; the commands tested here are the
terminal's own, so they are exercised through a ``MoCode``-shaped app (mocked)
and a ``Frontend``.
"""

import json
from unittest.mock import MagicMock

import pytest

from mocode.cli.commands import misc, model, session as session_cmds
from mocode.host.command import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from mocode.host.plugin.builtin.skills import make_skill_command
from mocode.host.session import Session

BUILTIN_COMMANDS = (*misc.commands, *model.commands, *session_cmds.commands)

_UNSET = object()


def _make_ctx(app=None, frontend=_UNSET, args="", commands=None) -> CommandContext:
    """Build a CommandContext. ``frontend=None`` means headless."""
    return CommandContext(
        app=app or MagicMock(),
        args=args,
        frontend=MagicMock() if frontend is _UNSET else frontend,
        commands=commands,
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


class TestCommandContext:
    def test_redraw_hands_the_conversation_to_the_frontend(self):
        app = MagicMock()
        frontend = MagicMock()
        _make_ctx(app=app, frontend=frontend).redraw()
        frontend.conversation_changed.assert_called_once_with(app.messages, app.tools)

    def test_redraw_is_a_no_op_when_headless(self):
        _make_ctx(frontend=None).redraw()  # must not raise


class TestQuitCommand:
    @pytest.mark.asyncio
    async def test_returns_exit(self):
        result = await _get_builtin_cmd("/quit").handler(_make_ctx())
        assert result is EXIT


class TestClearCommand:
    @pytest.mark.asyncio
    async def test_starts_a_new_session_and_redraws(self):
        app = MagicMock()
        frontend = MagicMock()

        result = await _get_builtin_cmd("/clear").handler(
            _make_ctx(app=app, frontend=frontend)
        )

        assert result is CONTINUE
        app.start_session.assert_called_once()
        frontend.conversation_changed.assert_called_once()


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_lists_registered_commands(self):
        registry = CommandRegistry()
        registry.register(_get_builtin_cmd("/quit"), _get_builtin_cmd("/help"))
        frontend = MagicMock()

        await _get_builtin_cmd("/help").handler(
            _make_ctx(frontend=frontend, commands=registry)
        )

        listed = frontend.info.call_args[0][0]
        assert "/quit" in listed and "/help" in listed

    @pytest.mark.asyncio
    async def test_headless_is_a_noop(self):
        registry = CommandRegistry()
        registry.register(_get_builtin_cmd("/quit"))

        result = await _get_builtin_cmd("/help").handler(
            _make_ctx(frontend=None, commands=registry)
        )

        assert result is CONTINUE


class TestExportCommand:
    @pytest.mark.asyncio
    async def test_exports_json_by_default(self, tmp_path, monkeypatch):
        session = _session()
        app = MagicMock()
        app.sessions.get_active.return_value = session
        app.agent.system_prompt = "You are helpful."
        frontend = MagicMock()

        monkeypatch.chdir(tmp_path)
        result = await _get_builtin_cmd("/export").handler(
            _make_ctx(app=app, frontend=frontend)
        )

        assert result is CONTINUE
        app.sessions.export_to_file.assert_called_once()
        assert "Exported 1 msgs" in frontend.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_export_md_format(self, tmp_path, monkeypatch):
        app = MagicMock()
        app.sessions.get_active.return_value = _session()
        app.agent.system_prompt = "You are helpful."

        monkeypatch.chdir(tmp_path)
        await _get_builtin_cmd("/export").handler(_make_ctx(app=app, args="md"))

        app.sessions.export_to_md.assert_called_once()
        assert app.sessions.export_to_md.call_args[0][1].suffix == ".md"

    @pytest.mark.asyncio
    async def test_nothing_to_export(self):
        app = MagicMock()
        app.sessions.get_active.return_value = None
        frontend = MagicMock()

        result = await _get_builtin_cmd("/export").handler(
            _make_ctx(app=app, frontend=frontend)
        )

        assert result is CONTINUE
        frontend.warn.assert_called_once()
        app.sessions.export_to_file.assert_not_called()


class TestResumeCommand:
    @pytest.mark.asyncio
    async def test_resume_from_an_exported_file(self, tmp_path):
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
        frontend = MagicMock()
        result = await _get_builtin_cmd("/resume").handler(
            _make_ctx(app=app, frontend=frontend, args=str(path))
        )

        assert result is CONTINUE
        app.start_session.assert_called_once_with(export_data["messages"])
        frontend.conversation_changed.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_rejects_an_invalid_file(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")

        app = MagicMock()
        frontend = MagicMock()
        await _get_builtin_cmd("/resume").handler(
            _make_ctx(app=app, frontend=frontend, args=str(path))
        )

        app.start_session.assert_not_called()
        frontend.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_without_a_frontend_does_nothing(self):
        app = MagicMock()
        result = await _get_builtin_cmd("/resume").handler(
            _make_ctx(app=app, frontend=None)
        )
        assert result is CONTINUE
        app.start_session.assert_not_called()


class TestSkillCommand:
    """`/skill:<name>` is contributed by a *host* plugin, so it works headless."""

    @pytest.mark.asyncio
    async def test_basic_load(self):
        cmd = make_skill_command(_skill("workflow", "DAG orchestration", "instructions here"))
        assert cmd.name == "/skill:workflow"
        assert cmd.description == "DAG orchestration"

        result = await cmd.handler(_make_ctx(frontend=None))

        assert result.kind is Kind.PROMPT
        assert "[Skill:workflow" in result.prompt
        assert "do NOT call the skill tool" in result.prompt
        assert "instructions here" in result.prompt
        assert "User request:" not in result.prompt

    @pytest.mark.asyncio
    async def test_with_user_request(self):
        cmd = make_skill_command(_skill("kami", "PDF typesetting", "typeset instructions"))
        result = await cmd.handler(_make_ctx(frontend=None, args="帮我做一份简历"))
        assert "User request: 帮我做一份简历" in result.prompt
        assert "typeset instructions" in result.prompt


def _session() -> Session:
    return Session(
        id="session_test",
        created_at="2025-01-01T00:00:00",
        updated_at="2025-01-01T00:00:00",
        workdir="/tmp",
        messages=[{"role": "user", "content": "hi"}],
    )


def _skill(name: str, description: str, content: str) -> MagicMock:
    skill = MagicMock()
    skill.metadata.name = name
    skill.metadata.description = description
    skill.load_content.return_value = content
    return skill

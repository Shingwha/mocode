"""CommandRegistry, CommandResult, and the terminal's own commands.

The command *contract* lives in the host; the commands tested here are the
terminal's, so they run against a real conversation and assert what the user
would have been shown — the notices the command published.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mocode.cli.commands import COMMANDS, misc, model, session as session_cmds
from mocode.core.events import Notice
from mocode.host.command import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.conversation import Conversation
from mocode.host.events import ConversationChanged
from mocode.host.plugin.builtin.skills import make_skill_command
from mocode.host.runtime import MoCode

BUILTIN_COMMANDS = COMMANDS

_UNSET = object()


@pytest.fixture
def conversation(tmp_path: Path) -> Conversation:
    config = Config(
        active_provider="test",
        active_model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                base_url="http://localhost",
                models={"test-model": ModelEntry()},
            )
        },
    )
    mc = MoCode(config=config, home=tmp_path / "home", plugin_dirs=[])
    return mc.new_conversation(cwd=tmp_path)


async def _run(
    command: Command,
    conversation: Conversation,
    *,
    args: str = "",
    commands: CommandRegistry | None = None,
) -> tuple[CommandResult, list]:
    """Run a command and collect what it published — what the user saw."""
    reader = conversation.subscribe()
    result = await command.handler(
        CommandContext(conversation=conversation, args=args, commands=commands)
    )
    seen = []
    while (event := reader.take()) is not None:
        seen.append(event)
    return result, seen


def _notices(events: list) -> list[Notice]:
    return [e for e in events if isinstance(e, Notice)]


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


class TestDispatch:
    @pytest.mark.asyncio
    async def test_a_named_command_runs(self, conversation: Conversation):
        from mocode.host.command import dispatch

        registry = CommandRegistry()
        registry.register(_get_builtin_cmd("/quit"))

        assert (await dispatch("/quit", conversation=conversation, commands=registry)) is EXIT

    @pytest.mark.asyncio
    async def test_a_command_gets_its_arguments(self, conversation: Conversation):
        from mocode.host.command import dispatch

        seen: list[str] = []

        async def handler(ctx):
            seen.append(ctx.args)
            return CONTINUE

        registry = CommandRegistry()
        registry.register(Command("/say", "say it", handler=handler))

        await dispatch("/say hello  world", conversation=conversation, commands=registry)
        assert seen == ["hello  world"]

    @pytest.mark.asyncio
    async def test_anything_else_is_a_prompt(self, conversation: Conversation):
        from mocode.host.command import dispatch

        result = await dispatch(
            "what is in this project?",
            conversation=conversation,
            commands=CommandRegistry(),
        )

        assert result.kind is Kind.PROMPT
        assert result.prompt == "what is in this project?"

    @pytest.mark.asyncio
    async def test_an_unknown_slash_word_is_a_prompt_too(self, conversation: Conversation):
        """The frontend decides what to say about it; the host does not guess."""
        from mocode.host.command import dispatch

        result = await dispatch(
            "/nope", conversation=conversation, commands=CommandRegistry()
        )

        assert result.kind is Kind.PROMPT


class TestQuitCommand:
    @pytest.mark.asyncio
    async def test_returns_exit(self, conversation: Conversation):
        result, _events = await _run(_get_builtin_cmd("/quit"), conversation)
        assert result is EXIT


class TestClearCommand:
    @pytest.mark.asyncio
    async def test_starts_a_new_session_and_says_so(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hello"})
        previous = conversation.id

        result, events = await _run(_get_builtin_cmd("/clear"), conversation)

        assert result is CONTINUE
        assert conversation.id != previous
        assert conversation.messages == []
        assert any(isinstance(e, ConversationChanged) for e in events)

    @pytest.mark.asyncio
    async def test_the_previous_session_survives_on_disk(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hello"})
        previous = conversation.id

        await _run(_get_builtin_cmd("/clear"), conversation)

        assert [s.id for s in conversation.list_sessions()] == [previous]


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_lists_registered_commands(self, conversation: Conversation):
        registry = CommandRegistry()
        registry.register(_get_builtin_cmd("/quit"), _get_builtin_cmd("/help"))

        _result, events = await _run(
            _get_builtin_cmd("/help"), conversation, commands=registry
        )

        listed = _notices(events)[0].message
        assert "/quit" in listed and "/help" in listed


class TestExportCommand:
    @pytest.mark.asyncio
    async def test_exports_json_into_the_project(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hi"})

        result, events = await _run(_get_builtin_cmd("/export"), conversation)

        assert result is CONTINUE
        written = list(Path(conversation.cwd).glob("session_*.json"))
        assert len(written) == 1
        assert "Exported 1 msgs" in _notices(events)[0].message

    @pytest.mark.asyncio
    async def test_export_md_format(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hi"})

        await _run(_get_builtin_cmd("/export"), conversation, args="md")

        assert len(list(Path(conversation.cwd).glob("session_*.md"))) == 1

    @pytest.mark.asyncio
    async def test_nothing_to_export(self, conversation: Conversation):
        _result, events = await _run(_get_builtin_cmd("/export"), conversation)

        assert [n.level for n in _notices(events)] == ["warn"]
        assert list(Path(conversation.cwd).glob("session_*")) == []


class TestResumeCommand:
    @pytest.mark.asyncio
    async def test_resume_from_an_exported_file(self, conversation: Conversation):
        export_data = {
            "system_prompt": "You are a coder.",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        }
        path = Path(conversation.cwd) / "session_test.json"
        path.write_text(json.dumps(export_data), encoding="utf-8")

        result, events = await _run(_get_builtin_cmd("/resume"), conversation, args=str(path))

        assert result is CONTINUE
        assert conversation.messages == export_data["messages"]
        assert any(isinstance(e, ConversationChanged) for e in events)

    @pytest.mark.asyncio
    async def test_resume_rejects_an_invalid_file(self, conversation: Conversation):
        path = Path(conversation.cwd) / "old.json"
        path.write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")

        _result, events = await _run(_get_builtin_cmd("/resume"), conversation, args=str(path))

        assert conversation.messages == []
        assert [n.level for n in _notices(events)] == ["warn"]

    @pytest.mark.asyncio
    async def test_a_bare_resume_says_so_when_there_is_nothing_to_resume(
        self, conversation: Conversation
    ):
        _result, events = await _run(_get_builtin_cmd("/resume"), conversation)

        assert [n.message for n in _notices(events)] == ["No sessions found."]

    @pytest.mark.asyncio
    async def test_a_bare_resume_without_a_terminal_does_nothing(
        self, conversation: Conversation
    ):
        """No picker, no pipe to draw it into — and no traceback either."""
        other = conversation.runtime.new_conversation(cwd=conversation.cwd)
        other.messages.append({"role": "user", "content": "an older session"})
        other.save()

        conversation.messages.append({"role": "user", "content": "current"})
        before = conversation.id

        result, events = await _run(_get_builtin_cmd("/resume"), conversation)

        assert result is CONTINUE
        assert conversation.id == before
        assert conversation.messages == [{"role": "user", "content": "current"}]
        assert events == []


class TestModelCommand:
    @pytest.mark.asyncio
    async def test_without_a_terminal_it_leaves_the_model_alone(
        self, conversation: Conversation
    ):
        _result, events = await _run(_get_builtin_cmd("/model"), conversation)

        assert conversation.model_name == "test-model"
        assert events == []


class TestSkillCommand:
    """`/skill:<name>` is contributed by a *host* plugin, so it works headless."""

    @pytest.mark.asyncio
    async def test_basic_load(self, conversation: Conversation):
        cmd = make_skill_command(_skill("workflow", "DAG orchestration", "instructions here"))
        assert cmd.name == "/skill:workflow"
        assert cmd.description == "DAG orchestration"

        result, _events = await _run(cmd, conversation)

        assert result.kind is Kind.PROMPT
        assert "[Skill:workflow" in result.prompt
        assert "do NOT call the skill tool" in result.prompt
        assert "instructions here" in result.prompt
        assert "User request:" not in result.prompt

    @pytest.mark.asyncio
    async def test_with_user_request(self, conversation: Conversation):
        cmd = make_skill_command(_skill("kami", "PDF typesetting", "typeset instructions"))

        result, _events = await _run(cmd, conversation, args="帮我做一份简历")

        assert "User request: 帮我做一份简历" in result.prompt
        assert "typeset instructions" in result.prompt

    @pytest.mark.asyncio
    async def test_an_empty_skill_says_so(self, conversation: Conversation):
        _result, events = await _run(make_skill_command(_skill("empty", "d", "")), conversation)

        assert [n.level for n in _notices(events)] == ["warn"]


def _skill(name: str, description: str, content: str) -> MagicMock:
    skill = MagicMock()
    skill.metadata.name = name
    skill.metadata.description = description
    skill.load_content.return_value = content
    return skill


def test_the_terminal_ships_the_commands_it_claims(monkeypatch):
    """The plugin's description lists what it actually registers."""
    from mocode.cli.plugin import PLUGIN

    names = {c.name for c in (*misc.commands, *model.commands, *session_cmds.commands)}
    described = set(PLUGIN.description.replace("Terminal commands: ", "").split())
    assert described <= names

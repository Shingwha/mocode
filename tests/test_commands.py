"""CommandRegistry, CommandResult, and the terminal's own commands.

The command *contract* lives in the host; the commands tested here are the
terminal's — the ones that need a picker or a clipboard. What a conversation
offers itself (/export, /clear, /help) arrives from the host's built-in
plugins and is tested in test_builtin_plugins.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mocode.cli.commands import COMMANDS
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
from mocode.host.conversation import Conversation
from mocode.host.events import ConversationChanged
from mocode.host.plugin.builtin.skills import make_skill_command
from mocode.host.runtime import MoCode

BUILTIN_COMMANDS = COMMANDS


@pytest.fixture
def conversation(mc) -> Conversation:
    return mc.new_conversation(cwd=mc.home.parent)


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
        assert "/quit" in names


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


def test_the_terminal_registers_its_commands_on_a_fresh_registry():
    """Every command the terminal ships is one build() away for any registry."""
    from mocode.cli.plugin import BuiltinCommands

    registry = CommandRegistry()
    BuiltinCommands().build(_FakeCLI(registry))
    shipped = {c.name for c in COMMANDS}
    assert shipped <= {c.name for c in registry.all()}


class _FakeCLI:
    """What BuiltinCommands needs from a CLIApp: the command registry."""

    def __init__(self, commands):
        self.commands = commands

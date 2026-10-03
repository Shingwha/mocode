"""CommandRegistry, CommandResult, and the terminal's own commands.

The command *contract* lives in the host; the commands tested here are the
terminal's — the ones that need a picker or a clipboard. What a conversation
offers itself (/export, /clear, /help) arrives from the host's built-in
plugins and is tested in test_builtin_plugins.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mocode.cli.commands import COMMANDS
from mocode.host.command import (
    CONTINUE,
    EXIT,
    Command,
    CommandRegistry,
    CommandResult,
    Kind,
)
from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.conversation import Conversation
from mocode.host.events import ConversationChanged
from mocode.host.plugin.builtin.skills import make_skill_command

from .conftest import notices, run_command

BUILTIN_COMMANDS = COMMANDS


@pytest.fixture
def conversation(mc) -> Conversation:
    return mc.new_conversation(cwd=mc.home.parent)


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
    async def test_a_named_command_runs(self, conversation: Conversation):
        registry = CommandRegistry()
        registry.register(_get_builtin_cmd("/quit"))

        assert (await registry.dispatch("/quit", conversation=conversation)) is EXIT

    async def test_a_command_gets_its_arguments(self, conversation: Conversation):
        seen: list[str] = []

        async def handler(ctx):
            seen.append(ctx.args)
            return CONTINUE

        registry = CommandRegistry()
        registry.register(Command("/say", "say it", handler=handler))

        await registry.dispatch("/say hello  world", conversation=conversation)
        assert seen == ["hello  world"]

    async def test_anything_else_is_a_prompt(self, conversation: Conversation):
        result = await CommandRegistry().dispatch(
            "what is in this project?", conversation=conversation
        )

        assert result.kind is Kind.PROMPT
        assert result.prompt == "what is in this project?"

    async def test_an_unknown_slash_word_is_a_prompt_too(self, conversation: Conversation):
        """The frontend decides what to say about it; the host does not guess."""
        result = await CommandRegistry().dispatch(
            "/nope", conversation=conversation
        )

        assert result.kind is Kind.PROMPT


class TestQuitCommand:
    async def test_returns_exit(self, conversation: Conversation):
        result, _events = await run_command(_get_builtin_cmd("/quit"), conversation)
        assert result is EXIT


class TestResumeCommand:
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

        result, events = await run_command(_get_builtin_cmd("/resume"), conversation, args=str(path))

        assert result is CONTINUE
        assert conversation.messages == export_data["messages"]
        assert any(isinstance(e, ConversationChanged) for e in events)

    async def test_resume_rejects_an_invalid_file(self, conversation: Conversation):
        path = Path(conversation.cwd) / "old.json"
        path.write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")

        _result, events = await run_command(_get_builtin_cmd("/resume"), conversation, args=str(path))

        assert conversation.messages == []
        assert [n.level for n in notices(events)] == ["warn"]

    async def test_a_bare_resume_says_so_when_there_is_nothing_to_resume(
        self, conversation: Conversation
    ):
        _result, events = await run_command(_get_builtin_cmd("/resume"), conversation)

        assert [n.message for n in notices(events)] == ["No sessions found."]

    async def test_a_bare_resume_without_a_terminal_does_nothing(
        self, conversation: Conversation
    ):
        """No picker, no pipe to draw it into — and no traceback either."""
        other = conversation.runtime.new_conversation(cwd=conversation.cwd)
        other.messages.append({"role": "user", "content": "an older session"})
        other.save()

        conversation.messages.append({"role": "user", "content": "current"})
        before = conversation.id

        result, events = await run_command(_get_builtin_cmd("/resume"), conversation)

        assert result is CONTINUE
        assert conversation.id == before
        assert conversation.messages == [{"role": "user", "content": "current"}]
        assert events == []


class TestModelCommand:
    async def test_without_a_terminal_it_leaves_the_model_alone(
        self, conversation: Conversation
    ):
        _result, events = await run_command(_get_builtin_cmd("/model"), conversation)

        assert conversation.model_name == "test-model"
        assert events == []

    async def test_the_picker_shows_ids_and_falls_back_to_them_for_titles(
        self, make_mc, tmp_path: Path, monkeypatch
    ):
        """Value is always the model id; the title is the display name when
        the entry declares one, and the id otherwise."""
        config = Config(
            provider="p",
            model="a",
            providers={
                "p": ProviderEntry(
                    name="P",
                    api_key="sk-x",
                    base_url="http://localhost",
                    models=[ModelEntry(id="a", name="Alpha"), ModelEntry(id="b")],
                )
            },
        )
        conversation = make_mc(config).new_conversation(cwd=tmp_path)
        conversation.runtime.config.save = lambda *a, **k: None
        seen: list[tuple[str, list, str | None]] = []
        answers = iter(["p", "b"])

        async def fake_select(title, choices, *, default=None, instruction=""):
            seen.append((title, choices, default))
            return next(answers)

        monkeypatch.setattr("mocode.cli.dialogs.select", fake_select)

        result, events = await run_command(_get_builtin_cmd("/model"), conversation)

        assert result is CONTINUE
        assert conversation.model_name == "b"
        provider_title, provider_choices, _ = seen[0]
        assert provider_title == "Select a provider:"
        assert [(c.title, c.value, c.description) for c in provider_choices] == [
            ("P", "p", "a, b")
        ]
        model_title, model_choices, model_default = seen[1]
        assert model_title == "Select a model for P:"
        assert [(c.title, c.value, c.description) for c in model_choices] == [
            ("Alpha", "a", "current"),
            ("b", "b", None),
        ]
        assert model_default == "a"
        assert [n.message for n in notices(events)] == ["Switched to P / b"]


class TestSkillCommand:
    """`/skill:<name>` is contributed by a *host* plugin, so it works headless."""

    async def test_basic_load(self, conversation: Conversation):
        cmd = make_skill_command(_skill("workflow", "DAG orchestration", "instructions here"))
        assert cmd.name == "/skill:workflow"
        assert cmd.description == "DAG orchestration"

        result, _events = await run_command(cmd, conversation)

        assert result.kind is Kind.PROMPT
        # 注入的 prompt 以 [Skill:<name>] 形态的指令块开头，随后是技能正文——
        # 顺序即契约，指令块与正文之间的文案不钉。
        assert re.search(r"\[Skill:workflow\b", result.prompt)
        assert result.prompt.index("[Skill:workflow") < result.prompt.index(
            "instructions here"
        )
        # 没有附带用户请求：prompt 止于技能正文
        assert result.prompt.rstrip().endswith("instructions here")

    async def test_with_user_request(self, conversation: Conversation):
        cmd = make_skill_command(_skill("kami", "PDF typesetting", "typeset instructions"))

        result, _events = await run_command(cmd, conversation, args="帮我做一份简历")

        # 用户请求拼在技能正文之后——顺序即契约，"User request" 标签不钉
        assert result.prompt.index("typeset instructions") < result.prompt.index(
            "帮我做一份简历"
        )

    async def test_an_empty_skill_says_so(self, conversation: Conversation):
        _result, events = await run_command(make_skill_command(_skill("empty", "d", "")), conversation)

        assert [n.level for n in notices(events)] == ["warn"]


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

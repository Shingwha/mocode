"""The host's built-in plugins — default prompt sections, session and help
commands.

They are ordinary plugins: loaded like any third-party one, contribute through
``build(ctx)``, and are disabled by the same ``plugins.<name>.enabled`` key.
So these tests run against a real conversation and assert what the model would
read (the prompt) and what the user would be shown (the notices a command
published) — the same contract a plugin written by anyone else is held to.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest_asyncio

from mocode.host.command import CONTINUE
from mocode.host.conversation import Conversation
from mocode.host.events import ConversationChanged
from mocode.host.runtime import MoCode

from .conftest import notices, run_command


@pytest_asyncio.fixture
async def conversation(mc) -> Conversation:
    # The builtin plugins load with every conversation; plugin_dirs=[] in the
    # mc fixture only keeps third-party ones out. The request surface
    # materializes here rather than at a first request, because these tests
    # read the prompt.
    conversation = mc.new_conversation(cwd=mc.home.parent)
    await conversation.prepare()
    return conversation


def _cmd(conversation: Conversation, name: str):
    cmd = conversation.commands.get(name)
    assert cmd is not None, f"{name} is not registered by a builtin plugin"
    return cmd


class TestDefaultPrompts:
    async def test_the_four_sections_render(self, conversation: Conversation):
        prompt = conversation.agent.system_prompt

        assert "<guidelines>" in prompt
        assert "<agents>" in prompt
        assert "<environment>" in prompt
        assert "<time>" in prompt
        assert f"cwd: {conversation.cwd}" in prompt
        assert "today:" in prompt
        assert "os:" in prompt

    async def test_the_prompt_does_not_repeat_the_tools(self, conversation: Conversation):
        """Tool schemas travel with every request; the prompt must not list them."""
        assert "<tool" not in conversation.agent.system_prompt

    async def test_agents_md_merges_global_and_project(self, mc: MoCode):
        mc.home.mkdir(parents=True, exist_ok=True)
        (mc.home / "AGENTS.md").write_text("user-wide rule", encoding="utf-8")
        project = mc.home.parent
        (project / "AGENTS.md").write_text("project rule", encoding="utf-8")

        conversation = mc.new_conversation(cwd=project)
        await conversation.prepare()

        assert "user-wide rule" in conversation.agent.system_prompt
        assert "project rule" in conversation.agent.system_prompt

    async def test_no_agents_md_renders_a_hint(self, conversation: Conversation):
        # a hint is rendered, not a specific sentence
        assert "AGENTS.md" in conversation.agent.system_prompt

    async def test_a_rebuild_re_reads_agents_md(self, mc: MoCode):
        project = mc.home.parent
        conversation = mc.new_conversation(cwd=project)
        await conversation.prepare()
        assert "Always use tabs." not in conversation.agent.system_prompt

        (project / "AGENTS.md").write_text("Always use tabs.", encoding="utf-8")
        conversation.rebuild_prompt()

        assert "Always use tabs." in conversation.agent.system_prompt

    async def test_a_rebuild_refreshes_the_date(self, mc: MoCode, monkeypatch):
        from mocode.host.plugin.builtin import default_prompts

        class frozen:
            @staticmethod
            def now():
                return dt.datetime(2026, 9, 20)

        monkeypatch.setattr(default_prompts, "datetime", frozen)
        conversation = mc.new_conversation(cwd=mc.home.parent)
        await conversation.prepare()

        # the frozen clock's date reaches the prompt — the weekday is the
        # calendar's business, what the plugin owns is the date itself
        assert "2026-09-20" in conversation.agent.system_prompt

    async def test_disabling_the_plugin_removes_its_sections(self, mc: MoCode):
        mc.config.plugins["default-prompts"] = {"enabled": False}

        conversation = mc.new_conversation(cwd=mc.home.parent)
        await conversation.prepare()

        prompt = conversation.agent.system_prompt

        assert "<guidelines>" not in prompt
        assert "<agents>" not in prompt
        assert "<environment>" not in prompt
        assert "<time>" not in prompt


class TestSessionPlugin:
    async def test_exports_json_into_the_project(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hi"})

        result, events = await run_command(_cmd(conversation, "/export"), conversation)

        assert result is CONTINUE
        written = list(Path(conversation.cwd).glob("session_*.json"))
        assert len(written) == 1
        # one message counted — the exported file itself is asserted above
        assert "1" in notices(events)[0].message

    async def test_export_md_format(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hi"})

        await run_command(_cmd(conversation, "/export"), conversation, args="md")

        assert len(list(Path(conversation.cwd).glob("session_*.md"))) == 1

    async def test_nothing_to_export(self, conversation: Conversation):
        _result, events = await run_command(_cmd(conversation, "/export"), conversation)

        assert [n.level for n in notices(events)] == ["warn"]
        assert list(Path(conversation.cwd).glob("session_*")) == []

    async def test_clear_starts_a_new_session_and_says_so(
        self, conversation: Conversation
    ):
        conversation.messages.append({"role": "user", "content": "hello"})
        previous = conversation.id

        result, events = await run_command(_cmd(conversation, "/clear"), conversation)

        assert result is CONTINUE
        assert conversation.id != previous
        assert conversation.messages == []
        assert any(isinstance(e, ConversationChanged) for e in events)

    async def test_the_previous_session_survives_on_disk(
        self, conversation: Conversation
    ):
        conversation.messages.append({"role": "user", "content": "hello"})
        previous = conversation.id

        await run_command(_cmd(conversation, "/clear"), conversation)

        assert [s.id for s in conversation.list_sessions()] == [previous]


def _effort_message(message: str) -> tuple[str, str]:
    """An /effort notice as ``(level, available)`` — the shape it carries.

    The wording is the user's; what the command promises is which level was
    chosen (or that none was) and which levels exist. Both are read out of
    the message rather than spelled back out.
    """
    head, _, available = message.partition(" — available: ")
    level = head.rsplit(": ", 1)[1] if ": " in head else head
    return level, available


class TestEffortPlugin:
    """``/effort`` is a host built-in: it works headless, needs only a
    conversation, and never writes config.json — the same contract the
    session and help commands are held to."""

    async def test_no_arg_reports_current_level_and_the_table(
        self, conversation: Conversation
    ):
        result, events = await run_command(_cmd(conversation, "/effort"), conversation)

        assert result is CONTINUE
        assert conversation.agent.model.effort is None
        [notice] = notices(events)
        # no level chosen yet, and the three levels are on the table
        level, available = _effort_message(notice.message)
        assert level == "(server default)"
        assert available.split(", ") == ["low", "medium", "high"]

    async def test_a_level_arg_switches_it_for_this_conversation(
        self, conversation: Conversation
    ):
        result, events = await run_command(
            _cmd(conversation, "/effort"), conversation, args="high"
        )

        assert result is CONTINUE
        assert conversation.agent.model.effort == "high"
        assert conversation.ctx.model.effort == "high"
        [notice] = notices(events)
        level, _available = _effort_message(notice.message)
        assert level == "high"

    async def test_an_unknown_level_warns_and_changes_nothing(
        self, conversation: Conversation
    ):
        result, events = await run_command(
            _cmd(conversation, "/effort"), conversation, args="ultra"
        )

        assert result is CONTINUE
        assert conversation.agent.model.effort is None
        warnings = notices(events)
        assert len(warnings) == 1
        assert warnings[0].level == "warn"
        # the unknown word is echoed back and the valid ones are named
        assert "ultra" in warnings[0].message
        assert "low" in warnings[0].message and "high" in warnings[0].message

    async def test_switching_never_writes_config(
        self, conversation: Conversation, monkeypatch
    ):
        saves: list = []
        monkeypatch.setattr(
            conversation.runtime.config, "save", lambda *a, **k: saves.append(1)
        )

        await run_command(_cmd(conversation, "/effort"), conversation, args="high")

        assert conversation.agent.model.effort == "high"
        assert saves == []


class TestHelpPlugin:
    async def test_lists_registered_commands(self, conversation: Conversation):
        from mocode.host.command import Command, CommandRegistry

        async def _noop(ctx):
            return CONTINUE

        registry = CommandRegistry()
        registry.register(
            Command("/quit", "quit", handler=_noop),
            Command("/clear", "clear", handler=_noop),
        )

        _result, events = await run_command(
            _cmd(conversation, "/help"), conversation, commands=registry
        )

        listed = notices(events)[0].message
        assert "/quit" in listed and "/clear" in listed

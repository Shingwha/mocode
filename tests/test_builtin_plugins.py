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

import pytest

from mocode.host.command import CONTINUE
from mocode.host.conversation import Conversation
from mocode.host.events import ConversationChanged
from mocode.host.runtime import MoCode

from .test_commands import _notices, _run


@pytest.fixture
def conversation(mc) -> Conversation:
    # The builtin plugins load with every conversation; plugin_dirs=[] in the
    # mc fixture only keeps third-party ones out.
    return mc.new_conversation(cwd=mc.home.parent)


def _cmd(conversation: Conversation, name: str):
    cmd = conversation.commands.get(name)
    assert cmd is not None, f"{name} is not registered by a builtin plugin"
    return cmd


class TestDefaultPrompts:
    def test_the_four_sections_render(self, conversation: Conversation):
        prompt = conversation.agent.system_prompt

        assert "<guidelines>" in prompt
        assert "<agents>" in prompt
        assert "<environment>" in prompt
        assert "<time>" in prompt
        assert f"cwd: {conversation.cwd}" in prompt
        assert "today:" in prompt
        assert "os:" in prompt

    def test_the_prompt_does_not_repeat_the_tools(self, conversation: Conversation):
        """Tool schemas travel with every request; the prompt must not list them."""
        assert "<tool" not in conversation.agent.system_prompt

    def test_agents_md_merges_global_and_project(self, mc: MoCode):
        mc.home.mkdir(parents=True, exist_ok=True)
        (mc.home / "AGENTS.md").write_text("user-wide rule", encoding="utf-8")
        project = mc.home.parent
        (project / "AGENTS.md").write_text("project rule", encoding="utf-8")

        prompt = mc.new_conversation(cwd=project).agent.system_prompt

        assert "user-wide rule" in prompt
        assert "project rule" in prompt

    def test_no_agents_md_renders_a_hint(self, conversation: Conversation):
        assert "No AGENTS.md files found yet." in conversation.agent.system_prompt

    def test_a_rebuild_re_reads_agents_md(self, mc: MoCode):
        project = mc.home.parent
        conversation = mc.new_conversation(cwd=project)
        assert "Always use tabs." not in conversation.agent.system_prompt

        (project / "AGENTS.md").write_text("Always use tabs.", encoding="utf-8")
        conversation.rebuild_prompt()

        assert "Always use tabs." in conversation.agent.system_prompt

    def test_a_rebuild_refreshes_the_date(self, mc: MoCode, monkeypatch):
        from mocode.host.plugin.builtin import default_prompts

        class frozen:
            @staticmethod
            def now():
                return dt.datetime(2026, 9, 20)

        monkeypatch.setattr(default_prompts, "datetime", frozen)
        conversation = mc.new_conversation(cwd=mc.home.parent)

        assert "today: 2026-09-20 (Sunday)" in conversation.agent.system_prompt

    def test_disabling_the_plugin_removes_its_sections(self, mc: MoCode):
        mc.config.plugins["default-prompts"] = {"enabled": False}

        prompt = mc.new_conversation(cwd=mc.home.parent).agent.system_prompt

        assert "<guidelines>" not in prompt
        assert "<agents>" not in prompt
        assert "<environment>" not in prompt
        assert "<time>" not in prompt


class TestSessionPlugin:
    @pytest.mark.asyncio
    async def test_exports_json_into_the_project(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hi"})

        result, events = await _run(_cmd(conversation, "/export"), conversation)

        assert result is CONTINUE
        written = list(Path(conversation.cwd).glob("session_*.json"))
        assert len(written) == 1
        assert "Exported 1 msgs" in _notices(events)[0].message

    @pytest.mark.asyncio
    async def test_export_md_format(self, conversation: Conversation):
        conversation.messages.append({"role": "user", "content": "hi"})

        await _run(_cmd(conversation, "/export"), conversation, args="md")

        assert len(list(Path(conversation.cwd).glob("session_*.md"))) == 1

    @pytest.mark.asyncio
    async def test_nothing_to_export(self, conversation: Conversation):
        _result, events = await _run(_cmd(conversation, "/export"), conversation)

        assert [n.level for n in _notices(events)] == ["warn"]
        assert list(Path(conversation.cwd).glob("session_*")) == []

    @pytest.mark.asyncio
    async def test_clear_starts_a_new_session_and_says_so(
        self, conversation: Conversation
    ):
        conversation.messages.append({"role": "user", "content": "hello"})
        previous = conversation.id

        result, events = await _run(_cmd(conversation, "/clear"), conversation)

        assert result is CONTINUE
        assert conversation.id != previous
        assert conversation.messages == []
        assert any(isinstance(e, ConversationChanged) for e in events)

    @pytest.mark.asyncio
    async def test_the_previous_session_survives_on_disk(
        self, conversation: Conversation
    ):
        conversation.messages.append({"role": "user", "content": "hello"})
        previous = conversation.id

        await _run(_cmd(conversation, "/clear"), conversation)

        assert [s.id for s in conversation.list_sessions()] == [previous]


class TestHelpPlugin:
    @pytest.mark.asyncio
    async def test_lists_registered_commands(self, conversation: Conversation):
        from mocode.host.command import Command, CommandRegistry

        async def _noop(ctx):
            return CONTINUE

        registry = CommandRegistry()
        registry.register(
            Command("/quit", "quit", handler=_noop),
            Command("/clear", "clear", handler=_noop),
        )

        _result, events = await _run(
            _cmd(conversation, "/help"), conversation, commands=registry
        )

        listed = _notices(events)[0].message
        assert "/quit" in listed and "/clear" in listed

"""cache-protect — the request prefix is pinned, the world's changes are
announced.

Two halves are under test: the frozen payload (what every request carries,
byte for byte) and the notices (what the model is told when the world moves).
Both are observed through a real conversation with a recording provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mocode.core.provider import Response, Usage
from mocode.host.runtime import MoCode

from .conftest import make_config
from .providers import MockProvider, tool_call_response
from .test_conversations import _answer, _project


def _conversation(mc: MoCode, cwd: Path, *responses: Response):
    conversation = mc.new_conversation(cwd=cwd)
    conversation.agent.provider = MockProvider(list(responses) or [_answer()])
    return conversation


def _notices(conversation) -> list[dict]:
    return [
        m
        for m in conversation.messages
        if m.get("role") == "user" and "[context update" in str(m.get("content", ""))
    ]


def _tool_names(payload: list[dict]) -> set[str]:
    return {schema["function"]["name"] for schema in payload}


class TestThePayloadIsPinned:
    @pytest.mark.asyncio
    async def test_a_switch_costs_no_request_change(self, mc: MoCode, tmp_path: Path):
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("one"), _answer("two")
        )

        await conversation.chat("first")
        conversation.tools.disable("read")
        await conversation.chat("second")

        provider = conversation.agent.provider
        assert provider.calls[0]["tools"] == provider.calls[1]["tools"]
        assert "read" in _tool_names(provider.calls[1]["tools"])

    @pytest.mark.asyncio
    async def test_a_disabled_tool_refuses_to_run(self, mc: MoCode, tmp_path: Path):
        conversation = _conversation(
            mc,
            _project(tmp_path, "a"),
            tool_call_response("read", '{"path": "notes.md"}'),
            _answer("fine"),
        )
        conversation.tools.disable("read")

        await conversation.chat("read it")

        results = [m for m in conversation.messages if m.get("role") == "tool"]
        assert results[0]["content"].startswith("denied:")

    @pytest.mark.asyncio
    async def test_a_rebuild_refreshes_the_payload_and_says_nothing(
        self, mc: MoCode, tmp_path: Path
    ):
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("one"), _answer("two")
        )
        await conversation.chat("first")
        conversation.tools.disable("read")

        conversation.rebuild_prompt()
        await conversation.chat("second")

        provider = conversation.agent.provider
        assert "read" not in _tool_names(provider.calls[-1]["tools"])
        # The model was just re-told everything; nothing is left to announce.
        assert _notices(conversation) == []


class TestTheWorldIsAnnounced:
    @pytest.mark.asyncio
    async def test_a_switch_off_is_one_line(self, mc: MoCode, tmp_path: Path):
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("one"), _answer("two")
        )
        await conversation.chat("first")

        conversation.tools.disable("read")
        await conversation.chat("second")

        assert "tool 'read' is now disabled" in _notices(conversation)[-1]["content"]

    @pytest.mark.asyncio
    async def test_a_switch_back_on_is_news_again(self, mc: MoCode, tmp_path: Path):
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("1"), _answer("2"), _answer("3")
        )
        await conversation.chat("first")
        conversation.tools.disable("read")
        await conversation.chat("second")

        conversation.tools.enable("read")
        await conversation.chat("third")

        assert "tool 'read' is now available" in _notices(conversation)[-1]["content"]

    @pytest.mark.asyncio
    async def test_a_change_reverted_between_turns_is_never_news(
        self, mc: MoCode, tmp_path: Path
    ):
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("1"), _answer("2")
        )
        await conversation.chat("first")

        conversation.tools.disable("read")
        conversation.tools.enable("read")
        await conversation.chat("second")

        assert _notices(conversation) == []

    @pytest.mark.asyncio
    async def test_an_in_place_edit_is_seen_and_diffed(
        self, mc: MoCode, tmp_path: Path
    ):
        """The schema is built from the Tool object, never the cached
        projection — an edit no registry operation invalidates is still seen."""
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("1"), _answer("2")
        )
        await conversation.chat("first")

        conversation.tools.get("read").description = "Read a file, with line numbers"
        await conversation.chat("second")

        notice = _notices(conversation)[-1]["content"]
        assert "tool 'read' changed:" in notice
        assert '-    "description": "Read a file and return' in notice
        assert '+    "description": "Read a file, with line numbers"' in notice

    @pytest.mark.asyncio
    async def test_a_tool_registered_late_is_announced_with_its_schema(
        self, mc: MoCode, tmp_path: Path
    ):
        from mocode.core.tool import Tool

        conversation = _conversation(mc, _project(tmp_path, "a"), _answer("one"), _answer("two"))
        await conversation.chat("hello")
        # Late means after the surface materialized — a tool registered
        # before the first turn is simply part of the initial interface.
        conversation.tools.register(
            Tool("grep", "Search file contents", {"pattern": {"type": "string"}}, lambda a: "")
        )

        await conversation.chat("again")

        notice = _notices(conversation)[0]["content"]
        assert "tool 'grep' is now available:" in notice
        assert '+    "name": "grep"' in notice

    @pytest.mark.asyncio
    async def test_the_notice_lands_before_the_users_message(
        self, mc: MoCode, tmp_path: Path
    ):
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("1"), _answer("2")
        )
        await conversation.chat("first")
        conversation.tools.disable("read")

        await conversation.chat("second")

        assert conversation.messages[-3]["content"].startswith("[context update")
        assert conversation.messages[-2]["content"] == "second"


class TestAResume:
    @pytest.mark.asyncio
    async def test_the_interface_comes_back_and_the_change_is_announced(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        conversation = _conversation(mc, project, _answer("one"))
        await conversation.chat("hi")
        session = conversation.save()

        second = mc.new_conversation(cwd=project)
        # The world moved between the save and the resume: read is off now.
        second.tools.disable("read")
        await second.load_session(session)
        # load_session restores the session's model, which replaces the
        # provider — the recorder goes back on afterwards.
        second.agent.provider = MockProvider([_answer("two")])
        await second.chat("again")

        provider = second.agent.provider
        assert "read" in _tool_names(provider.calls[0]["tools"])
        assert "tool 'read' is now disabled" in _notices(second)[-1]["content"]

    @pytest.mark.asyncio
    async def test_a_resume_with_no_change_says_nothing(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        conversation = _conversation(mc, project, _answer("one"))
        await conversation.chat("hi")
        session = conversation.save()

        second = mc.new_conversation(cwd=project)
        await second.load_session(session)
        second.agent.provider = MockProvider([_answer("two")])
        await second.chat("again")

        assert _notices(second) == []


class TestTheHostsSwitch:
    @pytest.mark.asyncio
    async def test_an_unpinned_runtime_keeps_the_payload_live(
        self, tmp_path: Path
    ):
        mc = MoCode(
            config=make_config(), home=tmp_path / "home", freeze_interface=False
        )
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("one"), _answer("two")
        )
        await conversation.chat("first")

        conversation.tools.disable("read")
        await conversation.chat("second")

        provider = conversation.agent.provider
        assert "read" not in _tool_names(provider.calls[1]["tools"])

    @pytest.mark.asyncio
    async def test_disabling_the_plugin_silences_the_notices(
        self, mc: MoCode, tmp_path: Path
    ):
        mc.config.plugins["cache-protect"] = {"enabled": False}
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("1"), _answer("2")
        )
        await conversation.chat("first")

        conversation.tools.disable("read")
        await conversation.chat("second")

        assert _notices(conversation) == []
        # The pin and the refusal are the registry's, not the plugin's.
        provider = conversation.agent.provider
        assert "read" in _tool_names(provider.calls[1]["tools"])


class TestUnified:
    def test_a_one_line_change_is_a_one_line_diff(self):
        from mocode.host.plugin.builtin.cache_protect import unified

        block = unified("the system prompt", "a\nb\nc", "a\nB\nc")

        assert block.splitlines()[0] == "--- the system prompt as last told"
        assert "-b" in block.splitlines()
        assert "+B" in block.splitlines()

    def test_an_addition_diffs_against_nothing(self):
        from mocode.host.plugin.builtin.cache_protect import unified

        block = unified("tool 'grep'", "", '{\n  "name": "grep"\n}')
        body = [
            line
            for line in block.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]

        assert "@@ -0,0 +1," in block
        assert body and all(line.startswith("+") for line in body)

    def test_a_removal_diffs_to_nothing(self):
        from mocode.host.plugin.builtin.cache_protect import unified

        block = unified("section 'time'", "today: old", "")

        assert "@@ -1 +0,0 @@" in block
        assert "-today: old" in block.splitlines()

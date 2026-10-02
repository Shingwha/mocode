"""cache-protect — the request prefix is pinned, the world's changes are
announced.

Two halves are under test: the frozen payload (what every request carries,
byte for byte) and the notices (what the model is told when the world moves).
Both are observed through a real conversation with a recording provider.
"""

from __future__ import annotations

from pathlib import Path

from mocode.host.runtime import MoCode
from mocode.testing import tool_call_response

from .conftest import project, updates, wired, wire


def _tool_names(payload: list[dict]) -> set[str]:
    return {schema["function"]["name"] for schema in payload}


class TestThePayloadIsPinned:
    async def test_a_switch_costs_no_request_change(self, wired, tmp_path: Path):
        conversation, _ = wired("one", "two", cwd=project(tmp_path, "a"))

        await conversation.chat("first")
        conversation.tools.disable("read")
        await conversation.chat("second")

        provider = conversation.agent.provider
        assert provider.calls[0]["tools"] == provider.calls[1]["tools"]
        assert "read" in _tool_names(provider.calls[1]["tools"])

    async def test_a_disabled_tool_refuses_to_run(self, wired, tmp_path: Path):
        conversation, _ = wired(tool_call_response("read", '{"path": "notes.md"}'), "fine", cwd=project(tmp_path, "a"))
        conversation.tools.disable("read")

        await conversation.chat("read it")

        results = [m for m in conversation.messages if m.get("role") == "tool"]
        assert results[0]["content"].startswith("denied:")

    async def test_a_rebuild_refreshes_the_payload_and_says_nothing(
        self, wired, tmp_path: Path
    ):
        conversation, _ = wired("one", "two", cwd=project(tmp_path, "a"))
        await conversation.chat("first")
        conversation.tools.disable("read")

        conversation.rebuild_prompt()
        await conversation.chat("second")

        provider = conversation.agent.provider
        assert "read" not in _tool_names(provider.calls[-1]["tools"])
        # The model was just re-told everything; nothing is left to announce.
        assert updates(conversation) == []


class TestTheWorldIsAnnounced:
    async def test_a_switch_off_is_one_line(self, wired, tmp_path: Path):
        conversation, _ = wired("one", "two", cwd=project(tmp_path, "a"))
        await conversation.chat("first")

        conversation.tools.disable("read")
        await conversation.chat("second")

        assert "tool 'read' is now disabled" in updates(conversation)[-1]["content"]

    async def test_a_pinned_derived_section_holds_the_prompt_and_announces_itself(
        self, wired, tmp_path: Path
    ):
        """K10, whole: the pin keeps the prompt byte-identical while the
        registry it derives from moves, and the notice carries the section's
        live diff — the model hears what the prompt cannot say."""
        from mocode.core.prompt import Section

        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"))

        def render(_ctx: dict) -> str:
            offered = sorted(conversation.tools.names())
            return "callable tools: " + ", ".join(offered)

        conversation.ctx.prompt_sections.append(
            Section("tools-sdk", render=render, priority=40, pinned=True, derived_from="tools")
        )
        await conversation.chat("first")
        frozen = conversation.agent.system_prompt

        conversation.tools.disable("read")
        await conversation.chat("second")

        # The prompt never moved — the pin did its job.
        assert conversation.agent.system_prompt == frozen
        notice = updates(conversation)[-1]["content"]
        assert "derived section 'tools-sdk' changed:" in notice
        assert "-callable tools: bash, bash_output, codemode, edit, kill_shell, read, skill, write" in notice
        assert "+callable tools: bash, bash_output, codemode, edit, kill_shell, skill, write" in notice

    async def test_a_rebuild_re_renders_the_pinned_section(self, wired, tmp_path: Path):
        from mocode.core.prompt import Section

        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"))

        def render(_ctx: dict) -> str:
            offered = sorted(conversation.tools.names())
            return "callable tools: " + ", ".join(offered)

        conversation.ctx.prompt_sections.append(
            Section("tools-sdk", render=render, priority=40, pinned=True, derived_from="tools")
        )
        await conversation.chat("first")

        conversation.tools.disable("read")
        conversation.rebuild_prompt()
        await conversation.chat("second")

        # A rebuild is the deliberate cache loss: the pin dropped, the
        # section re-rendered from the registry as it now stands.
        assert "callable tools: bash, bash_output, codemode, edit, kill_shell, skill, write" in conversation.agent.system_prompt
        assert updates(conversation) == []

    async def test_a_switch_back_on_is_news_again(self, wired, tmp_path: Path):
        conversation, _ = wired("1", "2", "3", cwd=project(tmp_path, "a"))
        await conversation.chat("first")
        conversation.tools.disable("read")
        await conversation.chat("second")

        conversation.tools.enable("read")
        await conversation.chat("third")

        assert "tool 'read' is now available" in updates(conversation)[-1]["content"]

    async def test_a_change_reverted_between_turns_is_never_news(
        self, wired, tmp_path: Path
    ):
        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"))
        await conversation.chat("first")

        conversation.tools.disable("read")
        conversation.tools.enable("read")
        await conversation.chat("second")

        assert updates(conversation) == []

    async def test_an_in_place_edit_is_seen_and_diffed(
        self, wired, tmp_path: Path
    ):
        """The schema is built from the Tool object, never the cached
        projection — an edit no registry operation invalidates is still seen."""
        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"))
        await conversation.chat("first")

        conversation.tools.get("read").description = "Read a file, with line numbers"
        await conversation.chat("second")

        notice = updates(conversation)[-1]["content"]
        assert "tool 'read' changed:" in notice
        assert '-    "description": "Read a file and return' in notice
        assert '+    "description": "Read a file, with line numbers"' in notice

    async def test_a_tool_registered_late_is_announced_with_its_schema(
        self, wired, tmp_path: Path
    ):
        from mocode.core.tool import Tool

        conversation, _ = wired("one", "two", cwd=project(tmp_path, "a"))
        await conversation.chat("hello")
        # Late means after the surface materialized — a tool registered
        # before the first turn is simply part of the initial interface.
        conversation.tools.register(
            Tool("grep", "Search file contents", {"pattern": {"type": "string"}}, lambda a: "")
        )

        await conversation.chat("again")

        notice = updates(conversation)[0]["content"]
        assert "tool 'grep' is now available:" in notice
        assert '+    "name": "grep"' in notice

    async def test_the_notice_lands_before_the_users_message(
        self, wired, tmp_path: Path
    ):
        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"))
        await conversation.chat("first")
        conversation.tools.disable("read")

        await conversation.chat("second")

        assert conversation.messages[-3]["content"].startswith("[context update")
        assert conversation.messages[-2]["content"] == "second"


class TestAResume:
    async def test_the_interface_comes_back_and_the_change_is_announced(
        self, mc: MoCode, tmp_path: Path, wired
    ):
        workdir = project(tmp_path, "a")
        conversation, _ = wired("one", cwd=workdir)
        await conversation.chat("hi")
        session = conversation.save()

        second = mc.new_conversation(cwd=workdir)
        # The world moved between the save and the resume: read is off now.
        second.tools.disable("read")
        await second.load_session(session)
        # load_session restores the session's model, which replaces the
        # provider — the recorder goes back on afterwards.
        wire(second, "two")
        await second.chat("again")

        provider = second.agent.provider
        assert "read" in _tool_names(provider.calls[0]["tools"])
        assert "tool 'read' is now disabled" in updates(second)[-1]["content"]

    async def test_a_resume_with_no_change_says_nothing(
        self, mc: MoCode, tmp_path: Path, wired
    ):
        workdir = project(tmp_path, "a")
        conversation, _ = wired("one", cwd=workdir)
        await conversation.chat("hi")
        session = conversation.save()

        second = mc.new_conversation(cwd=workdir)
        await second.load_session(session)
        wire(second, "two")
        await second.chat("again")

        assert updates(second) == []


class TestTheHostsSwitch:
    async def test_an_unpinned_runtime_keeps_the_payload_live(
        self, tmp_path: Path, make_mc, wired
    ):
        mc = make_mc(freeze_interface=False)
        conversation, _ = wired("one", "two", cwd=project(tmp_path, "a"), mc=mc)
        await conversation.chat("first")

        conversation.tools.disable("read")
        await conversation.chat("second")

        provider = conversation.agent.provider
        assert "read" not in _tool_names(provider.calls[1]["tools"])

    async def test_disabling_the_plugin_silences_the_notices(
        self, mc: MoCode, tmp_path: Path, wired
    ):
        mc.config.plugins["cache-protect"] = {"enabled": False}
        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"))
        await conversation.chat("first")

        conversation.tools.disable("read")
        await conversation.chat("second")

        assert updates(conversation) == []
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

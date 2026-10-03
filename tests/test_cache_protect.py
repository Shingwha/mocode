"""cache-protect — the request prefix is pinned, the world's changes are
announced.

Two halves are under test: the frozen payload (what every request carries,
byte for byte) and the notices (what the model is told when the world moves).
Both are observed through a real conversation with a recording provider.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mocode.host.runtime import MoCode
from mocode.testing import tool_call_response

from .conftest import project, updates, wired, wire


def _tool_names(payload: list[dict]) -> set[str]:
    return {schema["function"]["name"] for schema in payload}


#: 公告里点名工具状态迁移的那一行：哪个工具、朝哪个方向。
_STATE_LINE = re.compile(r"'([^']+)' is now (disabled|available|removed)")


def _tool_states(notice: str) -> dict[str, str]:
    """公告里"哪个工具动了、动去哪边"——解析出来的，不是逐字比的。

    返回 ``{工具名: 方向}``：一次公告只应点名真正移动过的工具。
    """
    return {m.group(1): m.group(2) for m in _STATE_LINE.finditer(notice)}


def _diff_sides(notice: str) -> tuple[str, str]:
    """一条公告里 diff 的两侧被改动的行（剥掉 +/- 行标记）。

    cache-protect 公告的唯一格式是 git 风格 unified diff；改动行按标记分到
    两侧重组出来供解析（见下方各测试），而不是把整句文案钉进断言。上下文行
    （无标记）与公告自身的说明行都不属于任何一侧的"改动"。
    """
    minus: list[str] = []
    plus: list[str] = []
    for line in notice.splitlines():
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
    return "\n".join(minus), "\n".join(plus)


def _json_entries(text: str) -> dict:
    """diff 一侧里可独立解析的 JSON 键值行 → ``{键: 值}``。

    就地改写时 hunk 只含改动区域，两侧都不是完整文档；但每一行改动本身是一
    个完整的 ``"key": value`` 对，逐个解析即可证明模型看到的是真实取值。
    """
    entries: dict = {}
    for line in text.splitlines():
        body = line.strip().rstrip(",")
        if not body.startswith('"'):
            continue
        try:
            entries.update(json.loads("{" + body + "}"))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return entries


def _listed_tools(text: str) -> set[str]:
    """本文件自己的渲染格式（``callable tools: a, b, c``）里的工具名集合。"""
    _, _, names = text.partition("callable tools: ")
    return {name.strip() for name in names.split(",") if name.strip()}


class TestThePayloadIsPinned:
    async def test_a_switch_costs_no_request_change_and_refuses_to_run(
        self, wired, tmp_path: Path
    ):
        """禁用不动请求面（两次请求带着同一份工具集合），但调用本身被拒绝。"""
        conversation, _ = wired(
            "one",
            tool_call_response("read", '{"path": "notes.md"}'),
            "fine",
            cwd=project(tmp_path, "a"),
        )
        await conversation.chat("first")
        conversation.tools.disable("read")

        await conversation.chat("read it")

        provider = conversation.agent.provider
        assert provider.calls[0]["tools"] == provider.calls[1]["tools"]
        assert "read" in _tool_names(provider.calls[1]["tools"])

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
    async def test_a_switch_is_announced_in_each_direction_and_a_revert_is_not(
        self, wired, tmp_path: Path
    ):
        """关掉再打开：两个方向各自公告一次，点名且只点名真正动了的工具；
        在 turn 之间改回去的，从来不是新闻。"""
        conversation, _ = wired("1", "2", "3", cwd=project(tmp_path, "a"))
        await conversation.chat("first")
        conversation.tools.disable("read")
        await conversation.chat("second")

        # 公告点名且只点名真正动了的那个工具
        assert _tool_states(updates(conversation)[-1]["content"]) == {"read": "disabled"}

        conversation.tools.enable("read")
        await conversation.chat("third")

        assert _tool_states(updates(conversation)[-1]["content"]) == {"read": "available"}

        # Changed and reverted before any turn saw it: never announced.
        reverted, _ = wired("1", "2", cwd=project(tmp_path, "a"))
        await reverted.chat("first")

        reverted.tools.disable("read")
        reverted.tools.enable("read")
        await reverted.chat("second")

        assert updates(reverted) == []

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
        offered = set(conversation.tools.names())

        conversation.tools.disable("read")
        await conversation.chat("second")

        # The prompt never moved — the pin did its job.
        assert conversation.agent.system_prompt == frozen
        notice = updates(conversation)[-1]["content"]
        assert re.search(r"derived section 'tools-sdk'", notice)
        # Announced diff, parsed: what the section read like against what it
        # reads now — the registry's move as a set difference, not a sentence.
        before, after = _diff_sides(notice)
        assert _listed_tools(before) == offered
        assert _listed_tools(after) == set(conversation.tools.names())
        assert offered - set(conversation.tools.names()) == {"read"}

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

        # A rebuild is the deliberate cache loss: the pin dropped, the section
        # re-rendered from the registry as it now stands — parsed out of the
        # prompt, not quoted back from it.
        section = re.search(
            r"<tools-sdk>\n(.*?)\n</tools-sdk>", conversation.agent.system_prompt, re.S
        )
        assert section is not None
        assert _listed_tools(section.group(1)) == set(conversation.tools.names())
        assert updates(conversation) == []

    async def test_an_in_place_edit_is_seen_and_diffed(
        self, wired, tmp_path: Path
    ):
        """The schema is built from the Tool object, never the cached
        projection — an edit no registry operation invalidates is still seen."""
        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"))
        await conversation.chat("first")

        read = conversation.tools.get("read")
        stale = read.to_schema()
        read.description = "Read a file, with line numbers"
        await conversation.chat("second")

        notice = updates(conversation)[-1]["content"]
        assert re.search(r"tool 'read'", notice)
        before, after = _diff_sides(notice)
        # 改动行本身是 JSON 键值对：解析它们，证明模型看到的是旧/新 schema 的
        # 真实取值——被改的是 description 这一个键，值来自工具对象的两次投影。
        assert _json_entries(before) == {"description": stale["function"]["description"]}
        assert _json_entries(after) == {
            "description": read.to_schema()["function"]["description"]
        }

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
        assert re.search(r"tool 'grep'", notice)
        before, after = _diff_sides(notice)
        assert before == ""  # nothing it was called before — the tool is new
        assert json.loads(after) == conversation.tools.get("grep").to_schema()

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
        assert _tool_states(updates(second)[-1]["content"]) == {"read": "disabled"}

        # 存档之后世界没动：同一条 resume 路径，一条公告都没有
        still, _ = wired("one", cwd=workdir)
        await still.chat("hi")
        untouched = still.save()

        third = mc.new_conversation(cwd=workdir)
        await third.load_session(untouched)
        wire(third, "two")
        await third.chat("again")

        assert updates(third) == []


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
    def test_the_diff_measures_the_change_in_both_directions(self):
        """一行改动是一行 diff，报头点名被 diff 的块；新增越过空侧而来，
        删除向空侧而去——正文按标记量出改动。"""
        from mocode.host.plugin.builtin.cache_protect import unified

        block = unified("the system prompt", "a\nb\nc", "a\nB\nc")

        # The header names what the block diffs; the body measures the change.
        header, *body = block.splitlines()
        assert header.startswith("---") and "the system prompt" in header
        assert "-b" in body
        assert "+B" in body

        # 新增与删除都是越过空侧的 diff
        addition = unified("tool 'grep'", "", '{\n  "name": "grep"\n}')
        added = [
            line
            for line in addition.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]

        assert "@@ -0,0 +1," in addition
        assert added and all(line.startswith("+") for line in added)

        removal = unified("section 'time'", "today: old", "")

        assert "@@ -1 +0,0 @@" in removal
        assert "-today: old" in removal.splitlines()

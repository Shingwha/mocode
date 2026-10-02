"""Tests for the codemode builtin plugin — runtime, api, output, search,
plugin and the end-to-end contract."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mocode.core.tool import Tool, ToolError, ToolRegistry
from mocode.host.plugin.builtin.codemode.api import (
    Store,
    ToolBox,
    ToolCallError,
    build_env,
    describe_tool_entry,
    tool_entries,
)
from mocode.host.plugin.builtin.codemode.runtime import (
    RESTRICTED,
    CodemodeError,
    _ScriptExit,
    run_script,
)

from .conftest import echo_tool, make_agent


class _FakeOutput:
    """Stands in for codemode.output.Output until that module lands (T3)."""

    def __init__(self):
        self.items: list = []
        self.images: list = []

    def text(self, value):
        self.items.append(value if isinstance(value, str) else repr(value))

    def image(self, block):
        self.images.append(block)


def _failing_tool() -> Tool:
    async def fail(args):
        raise ToolError("nope")

    return Tool(
        name="fail",
        description="always fails",
        schema={"type": "object", "properties": {}},
        func=fail,
    )


# ── T1: runtime ─────────────────────────────────────────────


class TestRunScript:
    async def test_top_level_await_and_return(self):
        async def helper() -> int:
            await asyncio.sleep(0)
            return 42

        env = {"helper": helper}
        assert await run_script("await helper()\nreturn 21 * 2", env) == 42

    async def test_no_return_is_none(self):
        assert await run_script("x = 1 + 1", {}) is None

    async def test_script_sees_injected_env(self):
        assert await run_script("return a + b", {"a": 2, "b": 3}) == 5

    @pytest.mark.parametrize("script", ["", "   \n\t\n  "])
    async def test_empty_script(self, script: str):
        with pytest.raises(CodemodeError, match="script is empty"):
            await run_script(script, {})

    async def test_syntax_error_carries_location(self):
        with pytest.raises(SyntaxError) as exc_info:
            await run_script("def broken(:", {})
        assert "<codemode>" in str(exc_info.value)

    async def test_script_exception_propagates(self):
        # ValueError is on the restricted whitelist; the message and type
        # reach the caller, which renders the script-failed result.
        with pytest.raises(ValueError, match="boom"):
            await run_script("raise ValueError('boom')", {})

    async def test_script_exit_propagates(self):
        # The injected exit() raises _ScriptExit; run_script lets it through
        # and the tool layer turns it into success.
        def exit():
            raise _ScriptExit()

        with pytest.raises(_ScriptExit):
            await run_script("exit()", {"exit": exit})

    async def test_cancellation_propagates(self):
        async def blocker():
            await asyncio.sleep(60)

        task = asyncio.create_task(run_script("await blocker()", {"blocker": blocker}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_restricted_builtins_allow_common_ops(self):
        assert await run_script("return sum([1, 2, 3])", {}) == 6
        assert await run_script("return sorted([3, 1, 2])", {}) == [1, 2, 3]

    @pytest.mark.parametrize("name", ["open", "__import__", "eval", "exec", "input"])
    async def test_restricted_builtins_hide_dangerous_names(self, name: str):
        with pytest.raises(NameError):
            await run_script(f"{name}", {})

    async def test_restricted_has_no_exit(self):
        # Python's own exit/quit are absent; the injected exit() is the only one.
        with pytest.raises(NameError):
            await run_script("exit", {})

    async def test_restricted_is_not_the_real_builtins(self):
        assert RESTRICTED != __builtins__ if isinstance(__builtins__, dict) else True
        assert "open" not in RESTRICTED
        assert "len" in RESTRICTED


# ── T2: api — ToolBox, discovery, store ─────────────────────


def _box(*tools, parent="call_1") -> ToolBox:
    agent = make_agent(*tools)
    return ToolBox(agent.tool_registry, agent.dispatcher, parent)


class TestToolBox:
    async def test_getitem_calls_tool(self):
        box = _box(echo_tool())
        outcome = await box["echo"]({"value": "hi"})
        assert outcome.content == "echo:hi"
        assert outcome.status == "ok"
        assert str(outcome) == "echo:hi"

    async def test_getattr_calls_tool(self):
        box = _box(echo_tool())
        outcome = await box.echo({"value": "hi"})
        assert outcome.content == "echo:hi"
        assert box.calls == 1

    async def test_args_and_kwargs_merge(self):
        box = _box(echo_tool())
        outcome = await box.echo({"value": "from-args"}, value="from-kwargs")
        # kwargs win over the args dict
        assert outcome.content == "echo:from-kwargs"

    async def test_normalized_attr_finds_hyphenated_name(self):
        tool = echo_tool("weird-name")
        box = _box(tool)
        outcome = await box.weird_name({"value": "x"})
        assert outcome.content == "echo:x"
        outcome = await box["weird-name"]({"value": "y"})
        assert outcome.content == "echo:y"

    async def test_ambiguous_normalized_attr_is_unknown(self):
        # Both names normalize to "x_y" and neither IS "x_y" — the
        # normalized form stays unresolvable rather than guessing.
        box = _box(echo_tool("x-y"), echo_tool("x@y"))
        with pytest.raises(CodemodeError, match="unknown tool 'x_y'"):
            await box.x_y({"value": "x"})
        # exact names still work
        outcome = await box["x-y"]({"value": "x"})
        assert outcome.content == "echo:x"

    async def test_unknown_tool_message(self):
        box = _box(echo_tool())
        with pytest.raises(
            CodemodeError, match=r"unknown tool 'nope'; use search_tools\(\) or ALL_TOOLS"
        ):
            box["nope"]

    async def test_codemode_is_not_callable(self):
        registry = ToolRegistry()
        registry.register(echo_tool("codemode"))
        agent = make_agent()
        box = ToolBox(registry, agent.dispatcher, "call_1")
        with pytest.raises(CodemodeError, match="codemode cannot be called from a script"):
            box["codemode"]
        with pytest.raises(CodemodeError, match="codemode cannot be called from a script"):
            box.codemode

    async def test_failing_call_raises_with_result(self):
        box = _box(_failing_tool())
        with pytest.raises(ToolCallError) as exc_info:
            await box.fail({})
        error = exc_info.value
        assert str(error) == "fail: error: execution_error: nope"
        assert error.result.status == "error"
        assert error.result.error_code == "execution_error"
        assert box.calls == 1  # counted even though the call failed

    async def test_gather_with_return_exceptions(self):
        box = _box(echo_tool(), _failing_tool())
        outcomes = await asyncio.gather(
            box.echo({"value": "ok"}),
            box.fail({}),
            return_exceptions=True,
        )
        assert outcomes[0].content == "echo:ok"
        assert isinstance(outcomes[1], ToolCallError)
        assert box.calls == 2

    async def test_program_origin_parenting(self):
        agent = make_agent(echo_tool())
        box = ToolBox(agent.tool_registry, agent.dispatcher, "parent-9")
        await box.echo({"value": "x"})
        finished = [
            e for e in agent.channel.history() if e.type == "tool_call_finished"
        ]
        assert finished and all(e.origin == "program" for e in finished)
        assert all(e.parent_call_id == "parent-9" for e in finished)
        assert finished[0].call_id.startswith("parent-9:")


class TestDiscovery:
    def _registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(echo_tool())
        registry.register(
            Tool(
                name="hidden",
                description="model only",
                schema={"type": "object", "properties": {}},
                func=lambda args: "hidden",
                availability="model",
            )
        )
        registry.register(echo_tool("codemode"))
        return registry

    def test_tool_entries_exclude_codemode_and_model_only(self):
        entries = tool_entries(self._registry())
        assert [e["name"] for e in entries] == ["echo"]
        assert entries[0]["description"] == "echo"

    def test_describe_tool_entry(self):
        registry = self._registry()
        entry = describe_tool_entry(registry, "echo")
        assert entry["name"] == "echo"
        assert entry["schema"]["type"] == "object"
        assert describe_tool_entry(registry, "missing") is None

    def test_build_env_injects_frozen_names(self):
        agent = make_agent(echo_tool())
        output = _FakeOutput()
        store = Store({})
        env, toolbox = build_env(
            agent.tool_registry, agent.dispatcher, "call_1", output, store
        )
        for name in (
            "asyncio", "json", "re", "math", "datetime", "textwrap",
            "collections", "itertools", "functools",
            "tools", "text", "console", "image", "exit", "store", "load",
            "ALL_TOOLS", "search_tools", "describe_tool",
        ):
            assert name in env, name
        assert "models" not in env
        assert "describe_namespace" not in env
        assert [t["name"] for t in env["ALL_TOOLS"]] == ["echo"]
        env["console"].log("a", 1, "b")
        assert output.items == ["a 1 b"]
        hits = env["search_tools"]("echo")
        assert [h["name"] for h in hits] == ["echo"]
        assert env["describe_tool"]("echo")["name"] == "echo"
        assert env["describe_tool"]("missing") is None
        assert toolbox.calls == 0

    def test_search_tools_snapshot_is_not_live(self):
        agent = make_agent(echo_tool())
        output = _FakeOutput()
        env, _ = build_env(agent.tool_registry, agent.dispatcher, "c", output, Store({}))
        agent.tool_registry.register(echo_tool("late"))
        assert [t["name"] for t in env["ALL_TOOLS"]] == ["echo"]
        assert [t["name"] for t in env["search_tools"]("late")] == []


class TestStore:
    def test_commit_applies_pending(self):
        backing = {"a": 1}
        store = Store(backing)
        store.store("b", [1, 2])
        store.store("a", None)  # delete
        store.commit()
        assert backing == {"b": [1, 2]}
        assert store.load("a") is None
        assert store.load("b") == [1, 2]

    def test_uncommitted_writes_stay_invisible(self):
        backing = {"a": 1}
        store = Store(backing)
        store.store("b", 2)
        assert store.load("b") == 2  # overlay wins
        assert backing == {"a": 1}  # backing untouched
        # a failing script just never commits — backing keeps its shape

    def test_single_value_limit(self):
        backing = {}
        store = Store(backing, max_value_chars=10)
        store.store("big", "x" * 100)
        with pytest.raises(CodemodeError, match="over the 10 limit"):
            store.commit()
        assert backing == {}  # nothing applied

    def test_total_limit(self):
        # json.dumps quotes strings: each 8-char value is 10 chars of JSON,
        # so 10 + 10 = 20 over a 15-char budget.
        backing = {"existing": "y" * 8}
        store = Store(backing, max_total_chars=15)
        store.store("new", "z" * 8)
        with pytest.raises(CodemodeError, match="over the 15 limit"):
            store.commit()
        assert backing == {"existing": "y" * 8}

    def test_total_limit_counts_resulting_store(self):
        # Values committed by earlier successful scripts count towards the
        # total — the check protects the persisted session, not one script.
        backing = {"existing": "y" * 8}
        store = Store(backing, max_total_chars=30)
        store.store("new", "z" * 8)
        store.commit()
        assert backing == {"existing": "y" * 8, "new": "z" * 8}

    def test_defaults_match_spec(self):
        store = Store({})
        assert store._max_value_chars == 262144
        assert store._max_total_chars == 1048576


# ── T3: output + rank ───────────────────────────────────────


from mocode.host.plugin.builtin.codemode.output import (
    Output,
    build_result,
    compose,
    truncate_body,
)
from mocode.host.plugin.builtin.codemode.search import normalize, rank


class TestOutput:
    def test_text_keeps_strings_and_jsonifies_rest(self):
        out = Output()
        out.text("plain")
        out.text({"a": 1})
        out.text([1, "x"])
        assert out.items == ['plain', '{"a": 1}', '[1, "x"]']

    def test_image_block_and_marker(self):
        out = Output()
        block = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        out.image(block)
        assert out.images == [block]
        assert out.items == ["[image: image/png]"]

    def test_image_data_url_and_object(self):
        out = Output()
        out.image("data:image/jpeg;base64,/9j/4AAQ")
        out.image({"image_url": "data:image/gif;base64,R0lGOD"})
        out.image({"image_url": "https://example.invalid/x.png"})
        assert out.items == [
            "[image: image/jpeg]",
            "[image: image/gif]",
            "[image: image]",
        ]

    def test_render_body_joins_items(self):
        out = Output()
        out.text("a")
        out.text("b")
        assert out.render_body() == "a\nb"
        assert Output().render_body() == ""


class TestTruncateBody:
    def test_short_body_untouched(self):
        assert truncate_body("hello", 12000) == ("hello", None)

    def test_nonpositive_limit_means_unlimited(self):
        assert truncate_body("hello", 0) == ("hello", None)
        assert truncate_body("hello", -5) == ("hello", None)

    def test_long_body_head_tail_marker_and_file(self):
        body = "".join(str(i % 10) for i in range(1000))
        text, path = truncate_body(body, 100)
        assert path is not None
        head, tail = body[:50], body[-50:]
        assert text == head + "\n…900 chars truncated…\n" + tail
        full = Path(path)
        assert full.name.startswith("mocode-codemode-") and full.suffix == ".txt"
        assert full.read_text(encoding="utf-8") == body
        full.unlink()

    def test_odd_limit_keeps_max_minus_one(self):
        text, _ = truncate_body("x" * 10, 7)
        # head 3 + marker + tail 3 → 3 + 4 + 3 with "…4 chars truncated…"
        assert text.startswith("xxx")
        assert text.endswith("xxx")


class TestCompose:
    def test_success_with_body(self):
        assert compose(True, 12, "line", None, None) == "Script completed in 12ms\nline"

    def test_success_empty_body_is_just_the_status_line(self):
        assert compose(True, 12, "", None, None) == "Script completed in 12ms"

    def test_failure_keeps_partial_output_then_error(self):
        text = compose(False, 5, "partial", ValueError("boom"), None)
        assert text == "Script failed in 5ms\npartial\nScript error: ValueError: boom"

    def test_failure_empty_body_has_no_blank_line(self):
        assert (
            compose(False, 5, "", ValueError("boom"), None)
            == "Script failed in 5ms\nScript error: ValueError: boom"
        )

    def test_full_output_path_appended_last(self):
        text = compose(True, 1, "b", None, "/tmp/full.txt")
        assert text.endswith("\nFull output: /tmp/full.txt")


class TestBuildResult:
    def test_success_details(self):
        out = Output()
        out.text("hi")
        result = build_result(ok=True, ms=3, output=out, error=None, tool_calls=2, max_chars=100)
        assert result.content == "Script completed in 3ms\nhi"
        assert result.details == {
            "ok": True,
            "images": [],
            "truncated": False,
            "full_output_path": None,
            "tool_calls": 2,
        }

    def test_failure_details(self):
        out = Output()
        result = build_result(
            ok=False, ms=3, output=out, error=CodemodeError("empty"), tool_calls=0, max_chars=100
        )
        assert result.content == "Script failed in 3ms\nScript error: CodemodeError: empty"
        assert result.details["ok"] is False

    def test_truncation_flows_into_result(self):
        out = Output()
        out.text("x" * 500)
        result = build_result(ok=True, ms=1, output=out, error=None, tool_calls=0, max_chars=100)
        assert result.details["truncated"] is True
        assert result.details["full_output_path"] is not None
        assert "…400 chars truncated…" in result.content
        assert "\nFull output: " in result.content
        Path(result.details["full_output_path"]).unlink()


class TestRank:
    def _entries(self):
        # Registered MCP names are normalized per the naming decision, so
        # the server segment carries underscores, never hyphens.
        return [
            {"name": "read_file", "description": "read a file from disk"},
            {"name": "bash", "description": "run a shell command"},
            {"name": "mcp__git__search", "description": "search github code"},
            {"name": "mcp__git_ops__search_things", "description": "search ops"},
        ]

    def test_empty_query_returns_registration_order(self):
        assert [t["name"] for t in rank("", self._entries())] == [
            "read_file",
            "bash",
            "mcp__git__search",
            "mcp__git_ops__search_things",
        ]

    def test_empty_query_respects_limit(self):
        assert len(rank("", self._entries(), limit=2)) == 2

    def test_name_hit_outranks_description_hit(self):
        hits = rank("bash", self._entries())
        assert hits[0]["name"] == "bash"

    def test_all_tokens_matched_gets_bonus_and_drives_order(self):
        # "search" hits two names; "code" hits one description — that one
        # matches every token and takes the top slot.
        hits = rank("search code", self._entries())
        assert hits[0]["name"] == "mcp__git__search"

    def test_unmatched_tools_drop_out(self):
        hits = rank("read file", self._entries())
        assert [t["name"] for t in hits] == ["read_file"]

    def test_ties_sort_by_name(self):
        hits = rank("search", self._entries())
        assert [t["name"] for t in hits] == [
            "mcp__git__search",
            "mcp__git_ops__search_things",
        ]

    def test_namespace_filter_uses_normalized_prefix(self):
        hits = rank("search", self._entries(), namespace="git-ops")
        assert [t["name"] for t in hits] == ["mcp__git_ops__search_things"]
        assert rank("search", self._entries(), namespace="nope") == []

    def test_normalize_replaces_invalid_identifier_chars(self):
        assert normalize("mcp__dev-radius__search") == "mcp__dev_radius__search"
        assert normalize("a b.c-d") == "a_b_c_d"


# ── T4: plugin + description ────────────────────────────────


from mocode.core.hook import ToolCallContext
from mocode.host.plugin.builtin.codemode import PLUGIN, CodemodePlugin
from mocode.host.plugin.builtin.codemode.description import DESCRIPTION
from mocode.host.plugin.builtin.codemode.plugin import codemode_tool, effective_options


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(echo_tool())
    return registry


class TestPlugin:
    def test_metadata(self):
        assert PLUGIN.name == "codemode"
        assert PLUGIN.description == "Run a Python script that calls other tools"
        assert isinstance(PLUGIN, CodemodePlugin)

    def test_package_init_exports(self):
        import mocode.host.plugin.builtin.codemode as package

        assert package.PLUGIN is PLUGIN
        assert package.CodemodePlugin is CodemodePlugin

    def test_description_teaches_python_dsl(self):
        assert "Python" in DESCRIPTION
        assert "tools.<name>(args)" in DESCRIPTION
        assert 'tools["exact-name"]' in DESCRIPTION
        assert "return_exceptions=True" in DESCRIPTION
        assert "store(key, value)" in DESCRIPTION
        assert "ALL_TOOLS" in DESCRIPTION
        assert "exit()" in DESCRIPTION
        assert "@options" in DESCRIPTION
        assert "cannot call itself" in DESCRIPTION

    def test_build_registers_model_only_tool(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        tool = host.ctx.tools.get("codemode")
        assert tool is not None
        assert tool.availability == "model"
        assert tool.tags == frozenset({"codemode"})
        assert tool.wants_context
        assert tool.schema["required"] == ["script"]
        assert "codemode" in host.ctx.tools.names(audience="model")
        assert "codemode" not in host.ctx.tools.names(audience="program")

    def test_build_does_not_self_report_source(self, plugin_host):
        # Stamping is the loader's job (tested in test_plugins.py) — the
        # plugin must only not claim one itself.
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        assert host.ctx.tools.get("codemode").source == ""


class TestRunTool:
    async def _run(self, host, script: str, options: dict | None = None):
        tool = host.ctx.tools.get("codemode")
        args = {"script": script}
        if options:
            args["options"] = options
        ctx = ToolCallContext(
            tool_name="codemode", tool_args=args, tool_call_id="call_cm_1"
        )
        return await tool.run_async(args, ctx)

    async def test_successful_script(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(
            host, 'r = await tools.echo({"value": "hi"})\nreturn r.content'
        )
        assert result.content.startswith("Script completed in ")
        assert result.content.endswith("echo:hi")
        assert result.details == {
            "ok": True,
            "images": [],
            "truncated": False,
            "full_output_path": None,
            "tool_calls": 1,
        }

    async def test_parallel_gather_in_script(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(
            host,
            "outcomes = await asyncio.gather("
            "tools.echo({'value': 'a'}), tools.echo({'value': 'b'}))"
            "\nreturn [o.content for o in outcomes]",
        )
        assert result.content.endswith('["echo:a", "echo:b"]')
        assert result.details["tool_calls"] == 2

    async def test_failed_script_keeps_partial_output(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'text("before")\nraise ValueError("boom")')
        assert result.content.startswith("Script failed in ")
        assert "\nbefore\n" in result.content
        assert result.content.endswith("Script error: ValueError: boom")
        assert result.details["ok"] is False

    async def test_failing_tool_call_fails_script(self, plugin_host):
        registry = ToolRegistry()
        registry.register(_failing_tool())
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        result = await self._run(host, 'await tools.fail({})')
        assert result.content.startswith("Script failed in ")
        assert "Script error: ToolCallError: fail: error: execution_error: nope" in result.content

    async def test_empty_script_error(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, "   ")
        assert "Script error: CodemodeError: script is empty" in result.content
        assert result.details["ok"] is False

    async def test_exit_ends_successfully(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'text("a")\nexit()\ntext("b")')
        assert result.details["ok"] is True
        assert result.content.endswith("\na")

    async def test_top_level_return_appended(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, "return {'n': 1}")
        assert result.content.endswith("\n{\"n\": 1}")

    async def test_image_block_in_details(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(
            host, 'image({"type": "image", "data": "AAAA", "mimeType": "image/png"})'
        )
        assert result.details["images"] == [
            {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        ]
        assert "[image: image/png]" in result.content

    async def test_store_commits_on_success_and_persists(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'store("k", {"v": 1})\ntext("saved")')
        assert result.details["ok"] is True
        assert host.ctx.plugin_state("codemode") == {"k": {"v": 1}}
        # a later call in the same conversation sees it
        result = await self._run(host, 'return load("k")')
        assert result.content.endswith('{"v": 1}')

    async def test_store_discarded_on_failure(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'store("k", 1)\nraise ValueError("x")')
        assert result.details["ok"] is False
        assert host.ctx.plugin_state("codemode") == {}

    async def test_store_limit_fails_without_applying(self, plugin_host):
        registry = _echo_registry()
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            config_kwargs={"plugins": {"codemode": {"store_max_value_chars": 5}}},
        )
        result = await self._run(host, 'store("k", "way too big")\ntext("done")')
        assert result.details["ok"] is False
        assert "CodemodeError" in result.content
        assert host.ctx.plugin_state("codemode") == {}

    async def test_max_output_chars_truncates(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(
            host, 'text("x" * 500)', options={"max_output_chars": 100}
        )
        assert result.details["truncated"] is True
        path = Path(result.details["full_output_path"])
        assert path.read_text(encoding="utf-8") == "x" * 500
        path.unlink()
        assert "\nFull output: " in result.content

    async def test_recursion_guard_in_script(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'await tools["codemode"]({"script": "pass"})')
        assert result.details["ok"] is False
        assert "codemode cannot be called from a script" in result.content
        result = await self._run(host, "return [t['name'] for t in ALL_TOOLS]")
        assert "codemode" not in result.content


class TestOptions:
    def test_effective_options_merges_comment_and_args(self):
        script = '# @options: {"timeout_ms": 5000, "max_output_chars": 100}\ntext("x")'
        merged = effective_options(script, {"timeout_ms": 9000})
        assert merged == {"timeout_ms": 9000, "max_output_chars": 100}

    def test_effective_options_ignores_bad_comment(self):
        script = "# @options: {not json}\n"
        assert effective_options(script, {"timeout_ms": 1}) == {"timeout_ms": 1}

    def test_effective_options_empty(self):
        assert effective_options("", None) == {}

    def test_policy_from_options_ms(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        policy = host.ctx.tools.get("codemode").policy
        assert policy({"script": "pass", "options": {"timeout_ms": 1500}}).timeout == 2
        assert policy({"script": "pass", "options": {"timeout_ms": 2000}}).timeout == 2
        assert policy({"script": "pass", "options": {"timeout_ms": 1}}).timeout == 1

    def test_policy_from_comment_line(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        policy = host.ctx.tools.get("codemode").policy
        args = {"script": '# @options: {"timeout_ms": 61000}\npass'}
        assert policy(args).timeout == 61

    def test_policy_from_config_timeout_s(self, plugin_host):
        host = plugin_host(
            plugins=[PLUGIN],
            tools=_echo_registry(),
            config_kwargs={"plugins": {"codemode": {"timeout_s": 30}}},
        )
        policy = host.ctx.tools.get("codemode").policy
        assert policy({"script": "pass"}).timeout == 30

    def test_policy_falls_back_to_none(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        policy = host.ctx.tools.get("codemode").policy
        assert policy({"script": "pass"}).timeout is None

"""Tests for the codemode builtin plugin — runtime, api, output, search,
plugin and the end-to-end contract."""

from __future__ import annotations

import asyncio
import builtins
import json
from pathlib import Path

import pytest

from mocode.core.tool import Tool, ToolError, ToolRegistry
from mocode.host.plugin.builtin.codemode.env import build_env
from mocode.host.plugin.builtin.codemode.result import (
    Batch,
    Result,
    ToolCallError,
    parallel,
)
from mocode.host.plugin.builtin.codemode.runtime import (
    _RESTRICTED_KEYS,
    RESTRICTED,
    CodemodeError,
    _ScriptExit,
    run_script,
    script_error_line,
)
from mocode.host.plugin.builtin.codemode.store import Store
from mocode.host.plugin.builtin.codemode.toolbox import (
    ToolBox,
    _mcp_short_name,
    describe_tool_entry,
    tool_entries,
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


def _slow_tool(delay: float) -> Tool:
    """A tool that answers after *delay* seconds — a cancelled call never
    answers, so deadline tests stay fast while a missed cancellation is
    still bounded by the sleep."""

    async def slow(args):
        await asyncio.sleep(delay)
        return f"slow:{args['value']}"

    return Tool(
        name="slow",
        description=f"sleep {delay}s then echo",
        schema={"type": "object", "properties": {"value": {"type": "string"}}},
        func=slow,
    )


def _order_tool(name: str, log: list) -> Tool:
    """A tool that records its start and end in *log* — for order assertions."""

    async def order_tool(args):
        log.append(f"{name}:start")
        await asyncio.sleep(0.05)
        log.append(f"{name}:end")
        return name

    return Tool(
        name=name,
        description=name,
        schema={"type": "object", "properties": {}},
        func=order_tool,
    )


def _gate_tool(
    name: str, started: list, both_started: asyncio.Event, release: asyncio.Event
) -> Tool:
    """A tool that returns only once *release* is set; two of these getting
    started proves the calls ran concurrently."""

    async def gate_tool(args):
        started.append(name)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return name

    return Tool(
        name=name,
        description=name,
        schema={"type": "object", "properties": {}},
        func=gate_tool,
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

    @pytest.mark.parametrize(
        "name",
        [
            "open",
            "eval",
            "exec",
            "compile",
            "input",
            "globals",
            "locals",
            "vars",
        ],
    )
    async def test_restricted_builtins_hide_dangerous_names(self, name: str):
        with pytest.raises(NameError):
            await run_script(f"{name}", {})

    async def test_restricted_builtins_allow_catching_by_name(self):
        # D4: the builtin exception classes are whitelisted, so a script
        # names what it catches instead of catching bare Exception.
        assert await run_script(
            "try:\n"
            "    raise RuntimeError('boom')\n"
            "except RuntimeError:\n"
            "    return 'caught'",
            {},
        ) == "caught"

    async def test_restricted_builtins_allow_dir(self):
        # field-findings P1-1: dir() opens introspection of the result.
        assert "append" in await run_script("return dir([])", {})

    @pytest.mark.parametrize(
        "name", ["BaseException", "KeyboardInterrupt", "SystemExit", "GeneratorExit"]
    )
    async def test_restricted_builtins_hide_cancellation_classes(self, name: str):
        # Not Exception subclasses — the collection rule itself leaves them
        # out, so a script cannot even name the class that would swallow
        # the cancellation unwinding a stopped or timed-out script.
        with pytest.raises(NameError):
            await run_script(name, {})

    async def test_script_cannot_swallow_a_cancellation_class(self):
        script = (
            "try:\n"
            "    raise ValueError('x')\n"
            "except BaseException:\n"
            "    return 'swallowed'"
        )
        with pytest.raises(NameError):
            await run_script(script, {})

    def test_restricted_whitelist_snapshot(self):
        # The frozen set stays, every builtin exception class joins by the
        # issubclass rule, dir comes along, and __import__ is the gated
        # gate — nothing else is in there.
        builtin_exceptions = {
            name
            for name, value in vars(builtins).items()
            if isinstance(value, type) and issubclass(value, Exception)
        }
        assert set(_RESTRICTED_KEYS) <= set(RESTRICTED)
        assert builtin_exceptions <= set(RESTRICTED)
        assert set(RESTRICTED) == set(_RESTRICTED_KEYS) | builtin_exceptions | {
            "dir",
            "__import__",
        }
        assert not {
            "BaseException",
            "KeyboardInterrupt",
            "SystemExit",
            "GeneratorExit",
        } & set(RESTRICTED)
        # the gate itself: whitelisted names import, the rest point at tools.*
        assert RESTRICTED["__import__"]("json") is json
        with pytest.raises(ImportError, match="tools.* facade"):
            RESTRICTED["__import__"]("os")

    async def test_gated_import_is_reachable_by_direct_call(self):
        # A script naming __import__ hits the same gate as the import
        # statement does.
        assert await run_script(
            "return __import__('json').dumps({'a': 1})", {}
        ) == '{"a": 1}'
        with pytest.raises(ImportError, match="not available"):
            await run_script("return __import__('os')", {})

    async def test_restricted_has_no_exit(self):
        # Python's own exit/quit are absent; the injected exit() is the only one.
        with pytest.raises(NameError):
            await run_script("exit", {})

    async def test_restricted_is_not_the_real_builtins(self):
        assert RESTRICTED != __builtins__ if isinstance(__builtins__, dict) else True
        assert "open" not in RESTRICTED
        assert "len" in RESTRICTED


class TestScriptErrorLine:
    """D10 — the wrapper offset: script line = reported line - 1."""

    async def test_error_in_script_own_code(self):
        with pytest.raises(ValueError, match="boom") as exc_info:
            await run_script("text('a')\ntext('b')\nraise ValueError('boom')", {"text": lambda v: None})
        assert script_error_line(exc_info.value) == 3

    async def test_error_in_nested_function_still_codemode(self):
        script = "def helper():\n    raise ValueError('inner')\n\nhelper()"
        with pytest.raises(ValueError, match="inner") as exc_info:
            await run_script(script, {})
        assert script_error_line(exc_info.value) == 2

    async def test_syntax_error_line(self):
        with pytest.raises(SyntaxError) as exc_info:
            await run_script("text('a')\ndef broken(:", {"text": lambda v: None})
        assert script_error_line(exc_info.value) == 2

    async def test_error_outside_script_has_no_line(self):
        # No traceback at all (a bare exception) and a CodemodeError raised
        # by run_script itself both point outside the script's frames.
        assert script_error_line(ValueError("bare")) is None
        with pytest.raises(CodemodeError) as exc_info:
            await run_script("", {})
        assert script_error_line(exc_info.value) is None


class TestImportGate:
    """D14 — import is gated to the injected modules; both ``import`` and
    ``from ... import ...`` pass, everything else fails with the tools
    facade in the message."""

    async def test_import_three_modules_in_one_statement(self):
        # field-findings P0-1 replay: the muscle-memory import line works.
        assert await run_script(
            "import re, asyncio, json\nreturn json.dumps({'ok': bool(re)})",
            {},
        ) == '{"ok": true}'

    async def test_from_import(self):
        assert await run_script(
            "from asyncio import gather\nreturn gather.__name__", {}
        ) == "gather"

    async def test_from_import_with_alias(self):
        assert await run_script(
            "from json import dumps as d\nreturn d({'a': 1})", {}
        ) == '{"a": 1}'

    async def test_import_rejected_with_tools_pointer(self):
        with pytest.raises(ImportError, match=r"import of 'os' is not available"):
            await run_script("import os", {})
        with pytest.raises(ImportError, match=r"tools.\* facade"):
            await run_script("import os", {})

    async def test_from_import_rejected(self):
        with pytest.raises(ImportError, match="not available"):
            await run_script("from os import path", {})

    async def test_submodule_import_rejected(self):
        # The gate matches exact module names — no submodules.
        with pytest.raises(ImportError, match="not available"):
            await run_script("import asyncio.exceptions", {})

    async def test_import_error_is_catchable_by_name(self):
        # ImportError is a whitelisted builtin exception, so scripts can
        # probe for an optional module without dying.
        assert await run_script(
            "try:\n    import os\nexcept ImportError:\n    return 'caught'",
            {},
        ) == "caught"

    def test_injected_modules_match_the_import_whitelist(self):
        from mocode.host.plugin.builtin.codemode.env import _MODULES
        from mocode.host.plugin.builtin.codemode.runtime import IMPORT_WHITELIST

        assert set(_MODULES) == set(IMPORT_WHITELIST)


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

    async def test_outcome_mapping_inside_a_script(self):
        # field-findings P0-4: res.get("content") is the idiom the docs
        # taught, so the outcome answers it.
        box = _box(echo_tool())
        outcome = await box.echo({"value": "hi"})
        assert outcome.get("content") == "echo:hi"
        assert outcome.get("nope") is None
        assert outcome.get("nope", "d") == "d"
        assert outcome["content"] == "echo:hi"
        assert "content" in outcome
        assert dict(outcome) == outcome.to_dict()

    async def test_mcp_full_and_short_names_agree(self):
        # field-findings P0-3: both entry points accept the folded full name
        # and its short form, through the same resolution.
        box = _box(echo_tool("mcp__k__bash"))
        assert (await box["mcp__k__bash"]({"value": "a"})).content == "echo:a"
        assert (await box.bash({"value": "b"})).content == "echo:b"
        assert (await box["bash"]({"value": "c"})).content == "echo:c"

    async def test_exact_name_beats_short_name(self):
        # A local tool and an MCP one share the "bash" short form; the
        # exact name is tier one of the resolution and wins.
        box = _box(echo_tool("bash"), echo_tool("mcp__k__bash"))
        assert (await box.bash({"value": "x"})).content == "echo:x"

    async def test_ambiguous_short_name_lists_candidates(self):
        # Two servers, one tool name — the short form refuses to guess and
        # names the candidates while the exact full names keep working.
        box = _box(echo_tool("mcp__k__bash"), echo_tool("mcp__other__bash"))
        expected = (
            r"unknown tool 'bash'; use search_tools\(\) or all_tools\(\) "
            r"— ambiguous short name, candidates: 'mcp__k__bash', "
            r"'mcp__other__bash'"
        )
        with pytest.raises(CodemodeError, match=expected):
            box.bash
        with pytest.raises(CodemodeError, match=expected):
            box["bash"]
        assert (await box["mcp__k__bash"]({"value": "x"})).content == "echo:x"
        assert (await box.mcp__other__bash({"value": "y"})).content == "echo:y"

    async def test_short_name_splits_on_the_last_separator(self):
        # A server raw-named "a//b" folds to "a__b", so the full name is
        # mcp__a__b__tool — the short name is the tool segment alone.
        box = _box(echo_tool("mcp__a__b__tool"))
        assert (await box.tool({"value": "x"})).content == "echo:x"

    async def test_hash_suffixed_name_keeps_a_unique_short_name(self):
        # The mcp collision suffix (…_<sha1[:6]>) rides along in the short
        # name, so two same-named tools of one server stay distinguishable.
        box = _box(echo_tool("mcp__k__tool_1a2b3c"))
        assert (await box.tool_1a2b3c({"value": "x"})).content == "echo:x"

    async def test_unknown_tool_message(self):
        box = _box(echo_tool())
        with pytest.raises(
            CodemodeError, match=r"unknown tool 'nope'; use search_tools\(\) or all_tools\(\)"
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

    def test_dir_lists_registered_tools_only(self):
        # dir(tools) is the callable catalogue: registered names, sorted,
        # and none of the forgiven built-ins.
        box = _box(echo_tool(), echo_tool("mcp__k__bash"))
        assert dir(box) == ["echo", "mcp__k__bash"]
        assert "describe_tool" not in dir(box)
        assert "store" not in dir(box)


class TestFacadeFallback:
    """D15 — the facade forgives: the built-in names resolve through
    ``tools.<name>`` to the very objects the bare names bind to, while the
    catalogue (dir, all_tools) still lists registered tools only."""

    def _env(self):
        agent = make_agent(echo_tool())
        output = _FakeOutput()
        env, box = build_env(
            agent.tool_registry, agent.dispatcher, "c", output, Store({})
        )
        return env, box, output

    def test_every_builtin_name_resolves_through_the_facade(self):
        env, box, _ = self._env()
        for name in (
            "describe_tool",
            "all_tools",
            "search_tools",
            "store",
            "load",
            "text",
            "console",
            "image",
            "print",
            "exit",
        ):
            assert getattr(box, name) is env[name], name
            assert box[name] is env[name], name

    async def test_facade_bound_names_work(self):
        env, box, output = self._env()
        box.text("via facade")
        box.print("a", 1)
        assert output.items == ["via facade", "a 1"]
        assert box.describe_tool("echo")["name"] == "echo"
        assert [t["name"] for t in box.all_tools()] == ["echo"]
        box.store("k", 1)
        assert env["load"]("k") == 1  # the same underlying store

    async def test_a_registered_tool_wins_over_the_facade(self):
        # An MCP tool whose short name is "store" resolves as a tool — the
        # built-in only fills the gaps the registered surface leaves.
        agent = make_agent(echo_tool(), echo_tool("mcp__k__store"))
        output = _FakeOutput()
        env, box = build_env(
            agent.tool_registry, agent.dispatcher, "c", output, Store({})
        )
        bound = box.store
        assert bound is not env["store"]
        outcome = await bound({"value": "x"})
        assert outcome.content == "echo:x"

    def test_unknown_tool_message_unchanged_by_the_facade(self):
        _, box, _ = self._env()
        with pytest.raises(
            CodemodeError,
            match=r"unknown tool 'nope'; use search_tools\(\) or all_tools\(\)",
        ):
            box.nope


class TestMcpShortNames:
    """The short-name rule: strip the mcp prefix, split on the LAST ``__``."""

    @pytest.mark.parametrize(
        "full, short",
        [
            ("mcp__k__bash", "bash"),
            ("mcp__a__b__tool", "tool"),  # server folded with __ (a//b → a__b)
            ("mcp____tool", "tool"),  # empty server segment
            ("mcp__k__tool_1a2b3c", "tool_1a2b3c"),  # hash-collision suffix
            ("bash", None),  # not an MCP name
            ("mcp__k", None),  # no tool segment at all
            ("mcp__k__", None),  # empty tool segment
        ],
    )
    def test_short_name_boundaries(self, full: str, short: str | None):
        assert _mcp_short_name(full) == short


class TestResult:
    """D17 — the outcome is a first-class Result: ok/tool/error accessors,
    json()/structured helpers and a readable repr on top of the Mapping
    protocol."""

    def _result(self) -> Result:
        return Result(
            ok=True,
            content="c",
            details={"exit_code": 0},
            tool="echo",
            error_code=None,
        )

    def test_accessors(self):
        result = self._result()
        assert result.ok is True
        assert result.content == "c"
        assert result.details == {"exit_code": 0}
        assert result.tool == "echo"
        assert result.status == "ok"
        assert result.error_code is None
        assert result.error is None
        assert str(result) == "c"

    def test_repr_ok_shows_tool_and_size(self):
        assert repr(self._result()) == "<Result ok echo 1 chars>"
        page = Result(ok=True, content="x" * 3141, tool="read")
        assert repr(page) == "<Result ok read 3.1k chars>"

    def test_repr_error_shows_tool_and_reason(self):
        failed = Result(ok=False, tool="fail", error="fail: error: execution_error: nope")
        assert repr(failed) == "<Result error fail: fail: error: execution_error: nope>"

    def test_json_parses_content(self):
        assert Result(content='{"a": 1, "b": [2]}').json() == {"a": 1, "b": [2]}
        assert Result(content="[1, 2]").json() == [1, 2]

    def test_json_failure_returns_a_diagnostic_string(self):
        result = Result(content="not json at all")
        message = result.json()
        assert isinstance(message, str)
        assert "not valid JSON" in message
        assert "not json at all" in message  # the content snippet is included

    def test_structured_reads_the_mcp_details_key(self):
        structured = {"rows": [{"id": 1}]}
        result = Result(content="rows", details={"structured_content": structured})
        assert result.structured == structured
        assert Result(content="plain").structured is None


class TestResultMapping:
    """D7 — the result answers the Mapping protocol, attributes unchanged."""

    def _result(self) -> Result:
        return Result(content="c", details={"exit_code": 0}, error_code=None)

    def test_the_four_methods(self):
        result = self._result()
        assert result.get("content") == "c"
        assert result.get("details") == {"exit_code": 0}
        assert result.get("status") == "ok"
        assert result.get("error_code") is None
        assert result["content"] == "c"
        assert list(result.keys()) == ["content", "details", "status", "error_code"]
        assert "status" in result
        assert "nope" not in result

    def test_get_defaults(self):
        result = self._result()
        assert result.get("nope") is None
        assert result.get("nope", "fallback") == "fallback"

    def test_unknown_key_raises_key_error(self):
        with pytest.raises(KeyError):
            self._result()["nope"]

    def test_dict_round_trip_matches_to_dict(self):
        assert dict(self._result()) == self._result().to_dict()

    def test_attributes_and_str_unchanged(self):
        result = self._result()
        assert result.content == "c"
        assert result.details == {"exit_code": 0}
        assert result.status == "ok"
        assert result.error_code is None
        assert str(result) == "c"


class TestParallel:
    """D18 — batch calls are first-class: per-call failure capture, order
    preserved, a per-batch concurrency that overrides the global cap."""

    async def test_all_success_keeps_argument_order(self):
        box = _box(echo_tool())
        rs = await parallel(
            box.echo({"value": "a"}),
            box.echo({"value": "b"}),
            box.echo({"value": "c"}),
        )
        assert isinstance(rs, Batch)
        assert len(rs) == 3
        assert [r.content for r in rs] == ["echo:a", "echo:b", "echo:c"]
        assert rs.ok == list(rs)
        assert rs.failed == []
        assert box.calls == 3

    async def test_mixed_failure_is_captured_not_raised(self):
        box = _box(echo_tool(), _failing_tool())
        rs = await parallel(box.echo({"value": "ok"}), box.fail({}))
        assert [r.ok for r in rs] == [True, False]  # argument order kept
        assert [r.content for r in rs.ok] == ["echo:ok"]
        bad = rs.failed[0]
        assert bad.tool == "fail"
        assert bad.status == "error"
        assert bad.error_code == "execution_error"
        assert "nope" in bad.error
        assert box.calls == 2

    async def test_all_failures(self):
        box = _box(_failing_tool())
        rs = await parallel(box.fail({}), box.fail({}))
        assert rs.ok == []
        assert len(rs.failed) == 2
        assert all(r.error for r in rs.failed)

    async def test_non_awaitable_argument_raises(self):
        box = _box(echo_tool())
        with pytest.raises(TypeError, match="expects awaitables"):
            await parallel(box.echo)  # the bound call, never awaited
        with pytest.raises(TypeError, match="expects awaitables"):
            await parallel("not a coroutine")

    async def test_bad_concurrency_raises(self):
        box = _box(echo_tool())
        for bad in (0, -1, 2.0, True, "8"):
            with pytest.raises(ValueError, match="concurrency"):
                await parallel(box.echo({"value": "x"}), concurrency=bad)

    async def test_concurrency_one_serializes_calls(self):
        log = []
        box = _box(_order_tool("a", log), _order_tool("b", log))
        rs = await parallel(box.a({}), box.b({}), concurrency=1)
        assert [r.ok for r in rs] == [True, True]
        assert log == ["a:start", "a:end", "b:start", "b:end"]

    async def test_concurrency_overrides_the_global_semaphore(self):
        # The global cap is one, but the batch asks for eight: both calls
        # still get in flight — the per-batch limit replaces the global one.
        started: list = []
        both_started = asyncio.Event()
        release = asyncio.Event()
        agent = make_agent(
            _gate_tool("gate_a", started, both_started, release),
            _gate_tool("gate_b", started, both_started, release),
        )
        box = ToolBox(
            agent.tool_registry, agent.dispatcher, "c", semaphore=asyncio.Semaphore(1)
        )
        task = asyncio.create_task(
            parallel(box.gate_a({}), box.gate_b({}), concurrency=8)
        )
        await asyncio.wait_for(both_started.wait(), 5)
        release.set()
        rs = await task
        assert [r.ok for r in rs] == [True, True]
        assert sorted(started) == ["gate_a", "gate_b"]


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

    def test_describe_tool_entry_refuses_non_callable(self):
        # D9: the same source of truth as the callable surface — the
        # program-audience projection minus codemode. A name outside it is
        # not described however registered it is.
        registry = self._registry()
        assert describe_tool_entry(registry, "hidden") is None  # model-only
        assert describe_tool_entry(registry, "codemode") is None  # never callable
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
            "all_tools", "search_tools", "describe_tool",
        ):
            assert name in env, name
        assert "ALL_TOOLS" not in env
        assert "models" not in env
        assert "describe_namespace" not in env
        assert [t["name"] for t in env["all_tools"]()] == ["echo"]
        env["console"].log("a", 1, "b")
        assert output.items == ["a 1 b"]
        hits = env["search_tools"]("echo")
        assert [h["name"] for h in hits] == ["echo"]
        # names_only returns the plain string table — sorted() works on it
        assert env["search_tools"]("echo", names_only=True) == ["echo"]
        assert env["search_tools"]("nope", names_only=True) == []
        assert env["describe_tool"]("echo")["name"] == "echo"
        assert env["describe_tool"]("missing") is None
        assert toolbox.calls == 0

    def test_all_tools_returns_a_copy_of_the_snapshot(self):
        # Field-findings P1-2: sorted(all_tools()) fails because entries are
        # dicts — the names table is names_only's job. A script mutating the
        # returned list must not corrupt the snapshot either.
        agent = make_agent(echo_tool())
        output = _FakeOutput()
        env, _ = build_env(agent.tool_registry, agent.dispatcher, "c", output, Store({}))
        entries = env["all_tools"]()
        entries.clear()
        assert [t["name"] for t in env["all_tools"]()] == ["echo"]

    def test_search_tools_snapshot_is_not_live(self):
        agent = make_agent(echo_tool())
        output = _FakeOutput()
        env, _ = build_env(agent.tool_registry, agent.dispatcher, "c", output, Store({}))
        agent.tool_registry.register(echo_tool("late"))
        assert [t["name"] for t in env["all_tools"]()] == ["echo"]
        assert [t["name"] for t in env["search_tools"]("late")] == []
        assert env["search_tools"]("late", names_only=True) == []


class TestCatalogueTrim:
    """D13 — catalogue entries preview their description at 80 characters
    (exactly-80 stays, empties get no ellipsis); ranking still reads the
    full text."""

    _LONG = "alpha " * 30 + "needle"  # 186 chars; "needle" hides past the cut

    @staticmethod
    def _desc_tool(name: str, description: str) -> Tool:
        return Tool(
            name=name,
            description=description,
            schema={"type": "object", "properties": {}},
            func=lambda args: name,
        )

    def _env(self):
        agent = make_agent(
            self._desc_tool("with_long_description", self._LONG),
            self._desc_tool("exact_eighty", "x" * 80),
            self._desc_tool("empty_description", ""),
        )
        output = _FakeOutput()
        env, _ = build_env(
            agent.tool_registry, agent.dispatcher, "c", output, Store({})
        )
        return env

    def test_all_tools_previews_at_eighty_chars(self):
        entries = {e["name"]: e["description"] for e in self._env()["all_tools"]()}
        assert entries["with_long_description"] == self._LONG[:80] + "…"
        assert entries["exact_eighty"] == "x" * 80  # exactly 80 is not cut
        assert entries["empty_description"] == ""  # empties get no ellipsis

    def test_search_tools_entries_are_previewed_too(self):
        hits = self._env()["search_tools"]("alpha")
        assert {h["name"] for h in hits} == {"with_long_description"}
        assert hits[0]["description"] == self._LONG[:80] + "…"

    def test_ranking_reads_the_full_description(self):
        # "needle" sits past character 80 — the tool still wins the query,
        # and only the returned entry is the cut preview.
        hits = self._env()["search_tools"]("needle")
        assert [h["name"] for h in hits] == ["with_long_description"]
        assert "needle" not in hits[0]["description"]

    def test_full_text_stays_available_via_describe_tool(self):
        env = self._env()
        full = env["describe_tool"]("with_long_description")
        assert full["description"] == self._LONG


class TestScriptPrint:
    """D8 — the script's ``print`` is one item in the output pipeline,
    never the host's stdout."""

    def _env(self):
        agent = make_agent(echo_tool())
        output = _FakeOutput()
        env, _ = build_env(
            agent.tool_registry, agent.dispatcher, "c", output, Store({})
        )
        return env, output

    def test_print_is_injected(self):
        env, _ = self._env()
        assert env["print"] is not builtins.print

    def test_print_joins_arguments_with_the_separator(self):
        env, output = self._env()
        env["print"]("a", "b")
        env["print"]("x", 1, "y", sep="-")
        assert output.items == ["a b", "x-1-y"]

    def test_print_jsonifies_non_strings(self):
        # the text() convention, per argument — not Python's repr; the
        # whole call is still one item
        env, output = self._env()
        env["print"]({"x": 1}, [1, "b"], None)
        assert output.items == ['{"x": 1} [1, "b"] null']

    def test_print_with_no_args_is_one_empty_item(self):
        env, output = self._env()
        env["print"]()
        assert output.items == [""]


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

    def test_long_body_head_tail_notice_and_file(self):
        body = "".join(str(i % 10) for i in range(1000))
        text, path = truncate_body(body, 100)
        assert path is not None
        head, tail = body[:50], body[-50:]
        assert text.startswith(head)
        assert text.endswith(tail)
        full = Path(path)
        assert full.name.startswith("mocode-codemode-") and full.suffix == ".txt"
        assert full.read_text(encoding="utf-8") == body
        full.unlink()

    def test_notice_is_imperative_and_self_contained(self):
        # D16: the notice must be unmissable — the omitted character count,
        # the file path, an explicit read-first instruction and the
        # tools.read hint all in one line.
        body = "x" * 500
        text, path = truncate_body(body, 100)
        assert "⚠ 400 chars truncated" in text
        assert "before relying on this output, read the full result" in text
        assert path in text
        assert "tools.read" in text

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
        assert "⚠ 400 chars truncated" in result.content
        assert "before relying on this output, read the full result" in result.content
        assert "tools.read" in result.content
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


from mocode.core.events import Notice
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
        assert 'tools["mcp__dev_radius__search"]' in DESCRIPTION
        assert "return_exceptions=True" in DESCRIPTION
        assert "store(key, value)" in DESCRIPTION
        assert "all_tools()" in DESCRIPTION
        assert "describe_tool(name)" in DESCRIPTION
        assert "exit()" in DESCRIPTION
        assert "@options" in DESCRIPTION
        assert "cannot call itself" in DESCRIPTION

    def test_description_pins_contract_phrases(self):
        # v2: one pin per behavior in the v2 contract, so a rewording that
        # loses a behavior fails here. Phrases, not a full snapshot — the
        # surrounding words stay free to move.
        # the script shape
        assert "top-level `await`" in DESCRIPTION
        assert "never wrap" in DESCRIPTION and "asyncio.run()" in DESCRIPTION
        assert "`asyncio.ensure_future`" in DESCRIPTION  # background tasks
        # Result is first-class
        assert ".ok" in DESCRIPTION
        assert ".json()" in DESCRIPTION
        assert ".structured" in DESCRIPTION
        assert ".tool" in DESCRIPTION
        assert 'res.get("content")' in DESCRIPTION  # Mapping access
        assert "ToolCallError" in DESCRIPTION
        assert "FAILED CALL RAISES" in DESCRIPTION  # asymmetry, big letters
        # parallel / Batch
        assert "parallel(" in DESCRIPTION
        assert ".ok`/`.failed" in DESCRIPTION
        assert "concurrency=N" in DESCRIPTION
        assert "return_exceptions=True" in DESCRIPTION
        # naming tiers and the forgiving facade
        assert 'tools["mcp__dev_radius__search"]' in DESCRIPTION
        assert "short name" in DESCRIPTION
        assert "candidates" in DESCRIPTION  # ambiguity lists them
        assert "exact names win" in DESCRIPTION
        assert "cannot call itself" in DESCRIPTION
        assert "dir(tools)" in DESCRIPTION
        assert "tools.describe_tool" in DESCRIPTION
        # output and the imperative truncation notice
        assert "print(...)" in DESCRIPTION  # print reaches the output
        assert "max_output_chars" in DESCRIPTION
        assert "before relying on this output" in DESCRIPTION
        assert "tools.read" in DESCRIPTION
        # store limits and the large-payload idiom
        assert "256KB" in DESCRIPTION and "1MB" in DESCRIPTION
        assert "no pre-truncation" in DESCRIPTION
        assert "store` the path" in DESCRIPTION
        # discovery surface
        assert "names_only=False" in DESCRIPTION  # discovery signature
        assert "script-start snapshot" in DESCRIPTION  # all_tools() snapshot
        assert "80-character previews" in DESCRIPTION  # catalogue trim
        # gated import
        assert "gated" in DESCRIPTION
        assert "from asyncio import gather" in DESCRIPTION
        assert "ImportError" in DESCRIPTION
        # deadline and concurrency
        assert "@options" in DESCRIPTION
        assert "timed_out" in DESCRIPTION  # explicit deadline path
        assert "partial output is lost" in DESCRIPTION  # dispatcher fallback
        assert "max_concurrency" in DESCRIPTION
        assert "default unlimited" in DESCRIPTION
        # error locations
        assert "(line N)" in DESCRIPTION
        # hard cut: the old surface is gone
        assert "ALL_TOOLS" not in DESCRIPTION  # renamed to all_tools() (D3)
        assert "ToolOutcome" not in DESCRIPTION  # renamed to Result (D17)
        assert "mcp__dev-radius" not in DESCRIPTION  # folded-name example

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

    async def test_parallel_in_script_captures_failures(self, plugin_host):
        registry = ToolRegistry()
        registry.register(echo_tool())
        registry.register(_failing_tool())
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        result = await self._run(
            host,
            "rs = await parallel(tools.echo({'value': 'a'}), tools.fail({}))\n"
            "text('%d ok / %d failed' % (len(rs.ok), len(rs.failed)))\n"
            "text(rs.failed[0].error)",
        )
        assert result.details["ok"] is True
        assert "1 ok / 1 failed" in result.content
        assert "nope" in result.content
        assert result.details["tool_calls"] == 2

    async def test_script_branches_on_exception_types_after_gather(self, plugin_host):
        # D4 end to end: the script names RuntimeError in an except clause
        # and isinstance-branches after gather(return_exceptions=True).
        registry = ToolRegistry()
        registry.register(echo_tool())
        registry.register(_failing_tool())
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        result = await self._run(
            host,
            "rows = await asyncio.gather("
            "tools.echo({'value': 'a'}), tools.fail({}), return_exceptions=True)\n"
            "good = [r for r in rows if not isinstance(r, Exception)]\n"
            "text('%d ok / %d failed' % (len(good), len(rows) - len(good)))\n"
            "try:\n"
            "    raise RuntimeError('inner')\n"
            "except RuntimeError:\n"
            "    text('caught RuntimeError')",
        )
        assert result.details["ok"] is True
        assert "1 ok / 1 failed" in result.content
        assert "caught RuntimeError" in result.content

    async def test_failed_script_keeps_partial_output(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'text("before")\nraise ValueError("boom")')
        assert result.content.startswith("Script failed in ")
        assert "\nbefore\n" in result.content
        assert result.content.endswith(
            'Script error (line 2): ValueError: boom\nraise ValueError("boom")'
        )
        assert result.details["ok"] is False

    async def test_error_reports_script_line_and_source(self, plugin_host):
        # D10: the wrapper's header line shifts reported lines by one; the
        # error names the real script line and shows that line's source.
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'text("a")\ntext("b")\nraise ValueError("boom")')
        assert result.details["ok"] is False
        assert "Script error (line 3): ValueError: boom\n" in result.content
        assert 'raise ValueError("boom")' in result.content

    async def test_error_line_inside_nested_function(self, plugin_host):
        # A failure in a function the script defined still lives in a
        # <codemode> frame — the reported line is the raise inside helper().
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        script = "def helper():\n    raise ValueError('inner')\n\nhelper()"
        result = await self._run(host, script)
        assert result.details["ok"] is False
        assert "Script error (line 2): ValueError: inner\n" in result.content
        assert "raise ValueError('inner')" in result.content

    async def test_syntax_error_reports_real_line(self, plugin_host):
        # The SyntaxError off-by-one: Python reports against the compiled
        # source, whose first line is the wrapper header — line 3 there is
        # line 2 of the script.
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'text("a")\ndef broken(:')
        assert result.details["ok"] is False
        assert "Script error (line 2): SyntaxError:" in result.content
        assert "\ndef broken(:" in result.content

    async def test_tool_call_error_reports_no_line(self, plugin_host):
        # The failure surfaced inside the tool box (ToolCallError), not in
        # the script's own code — the plain format stays.
        registry = ToolRegistry()
        registry.register(_failing_tool())
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        result = await self._run(host, "await tools.fail({})")
        assert result.details["ok"] is False
        assert "Script error: ToolCallError: fail: error: execution_error: nope" in result.content
        assert "Script error (line" not in result.content

    async def test_explicit_deadline_keeps_partial_output(self, plugin_host):
        # D6: with an explicit deadline the plugin's own wait_for fires
        # first and the result is a normal failure — partial output kept,
        # timed_out marker set, no error line, store writes discarded.
        registry = ToolRegistry()
        registry.register(_slow_tool(10))
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        result = await self._run(
            host,
            'store("k", 1)\ntext("before")\n'
            'r = await tools.slow({"value": "x"})\ntext("after")',
            options={"timeout_ms": 50},
        )
        assert result.details["ok"] is False
        assert result.details["timed_out"] is True
        assert "\nbefore\n" in result.content
        assert "\nafter\n" not in result.content
        assert "Script timed out after 1s." in result.content
        assert "Script error" not in result.content
        assert host.ctx.plugin_state("codemode") == {}

    async def test_deadline_result_is_no_dispatcher_timeout(self, plugin_host):
        # Through the dispatcher with room to spare, the fired deadline
        # comes back as an ordinary ok call — never a TOOL_TIMEOUT status.
        registry = ToolRegistry()
        registry.register(_slow_tool(10))
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        result = await host.ctx.agent.dispatcher.run(
            "codemode",
            {
                "script": 'await tools.slow({"value": "x"})',
                "options": {"timeout_ms": 50},
            },
            timeout=30,
        )
        assert result.status == "ok"
        assert "Script timed out after 1s." in result.content

    async def test_no_deadline_keeps_dispatcher_fallback(self, plugin_host):
        # Without an explicit deadline the plugin path is byte-identical to
        # before: the dispatcher's timeout cancels the call and the partial
        # output is lost.
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await host.ctx.agent.dispatcher.run(
            "codemode",
            {"script": 'text("before")\nawait asyncio.sleep(10)'},
            timeout=1,
        )
        assert result.status == "timeout"
        assert "before" not in result.content

    async def test_turn_cancellation_passthrough_with_deadline(self, plugin_host):
        # Cancelling the turn mid-script still propagates untouched even
        # when a deadline is set — it must not be converted into a timed-out
        # result (or swallowed).
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        tool = host.ctx.tools.get("codemode")
        args = {"script": "await asyncio.sleep(10)", "options": {"timeout_ms": 60000}}
        ctx = ToolCallContext(
            tool_name="codemode", tool_args=args, tool_call_id="call_cm_cancel"
        )
        task = asyncio.create_task(tool.run_async(args, ctx))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_script_own_timeout_error_is_not_the_deadline(self, plugin_host):
        # A script may raise TimeoutError itself; the cancelled-task check
        # keeps it the script's own error rather than the deadline marker.
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(
            host,
            "raise TimeoutError('self-inflicted')",
            options={"timeout_ms": 60000},
        )
        assert result.details["ok"] is False
        assert "timed_out" not in result.details
        assert "Script error (line 1): TimeoutError: self-inflicted" in result.content
        assert "Script timed out" not in result.content

    async def test_concurrency_cap_serializes_calls(self, plugin_host):
        # D5: max_concurrency=1 — the second call waits for the first to
        # finish, even under gather.
        log = []
        registry = ToolRegistry()
        registry.register(_order_tool("slow_a", log))
        registry.register(_order_tool("slow_b", log))
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            config_kwargs={"plugins": {"codemode": {"max_concurrency": 1}}},
        )
        result = await self._run(
            host, "await asyncio.gather(tools.slow_a({}), tools.slow_b({}))"
        )
        assert result.details["ok"] is True
        assert log == ["slow_a:start", "slow_a:end", "slow_b:start", "slow_b:end"]

    async def test_no_cap_runs_calls_concurrently(self, plugin_host):
        # The default (no max_concurrency) is unchanged: both calls are in
        # flight before either returns.
        started: list = []
        both_started = asyncio.Event()
        release = asyncio.Event()
        registry = ToolRegistry()
        registry.register(_gate_tool("gate_a", started, both_started, release))
        registry.register(_gate_tool("gate_b", started, both_started, release))
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        tool = host.ctx.tools.get("codemode")
        args = {
            "script": "await asyncio.gather(tools.gate_a({}), tools.gate_b({}))"
        }
        ctx = ToolCallContext(
            tool_name="codemode", tool_args=args, tool_call_id="call_cm_conc"
        )
        task = asyncio.create_task(tool.run_async(args, ctx))
        await asyncio.wait_for(both_started.wait(), 5)
        release.set()
        result = await task
        assert result.details["ok"] is True
        assert sorted(started) == ["gate_a", "gate_b"]

    @pytest.mark.parametrize("raw", [0, -3, "2", 2.0, True, []])
    async def test_invalid_concurrency_reported_and_ignored(self, plugin_host, raw):
        # D5: an unusable max_concurrency is reported once per conversation
        # and ignored — the calls still run uncapped.
        started: list = []
        both_started = asyncio.Event()
        release = asyncio.Event()
        registry = ToolRegistry()
        registry.register(_gate_tool("gate_a", started, both_started, release))
        registry.register(_gate_tool("gate_b", started, both_started, release))
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            config_kwargs={"plugins": {"codemode": {"max_concurrency": raw}}},
        )
        tool = host.ctx.tools.get("codemode")
        args = {
            "script": "await asyncio.gather(tools.gate_a({}), tools.gate_b({}))"
        }
        ctx = ToolCallContext(
            tool_name="codemode", tool_args=args, tool_call_id="call_cm_bad"
        )
        task = asyncio.create_task(tool.run_async(args, ctx))
        await asyncio.wait_for(both_started.wait(), 5)
        release.set()
        result = await task
        assert result.details["ok"] is True
        notices = [
            e for e in host.ctx.agent.channel.history() if isinstance(e, Notice)
        ]
        assert len(notices) == 1
        assert "max_concurrency" in notices[0].message
        # the warning is once per conversation, not per call
        result = await self._run(host, "pass")
        assert result.details["ok"] is True
        notices = [
            e for e in host.ctx.agent.channel.history() if isinstance(e, Notice)
        ]
        assert len(notices) == 1

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

    async def test_print_lands_in_the_result_not_stdout(self, plugin_host, capsys):
        # D8: print was whitelisted but wrote to the host's stdout, where
        # no script reader could ever see it. Three shapes — string,
        # several arguments, non-string — one item each, none on stdout.
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(
            host, 'print("hello")\nprint("a", 1, "b")\nprint({"k": 1})'
        )
        assert result.details["ok"] is True
        assert result.content.endswith('hello\na 1 b\n{"k": 1}')
        assert capsys.readouterr().out == ""

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
        assert "⚠ 400 chars truncated" in result.content
        assert "before relying on this output, read the full result" in result.content
        assert "tools.read" in result.content
        assert "\nFull output: " in result.content

    async def test_recursion_guard_in_script(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'await tools["codemode"]({"script": "pass"})')
        assert result.details["ok"] is False
        assert "codemode cannot be called from a script" in result.content
        result = await self._run(host, "return [t['name'] for t in all_tools()]")
        assert "codemode" not in result.content
        # the short name stays unreachable too — it resolves to codemode
        result = await self._run(host, 'return tools.codemode')
        assert result.details["ok"] is False
        assert "codemode cannot be called from a script" in result.content


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


# ── T5: end to end through a real turn ──────────────────────


from mocode.testing import call_tool, say, terminal


class TestEndToEnd:
    """A scripted model calls codemode inside a real turn — the
    program-origin contract is what the model-visible history must show."""

    async def _turn(self, plugin_host, script: str):
        host = plugin_host(
            plugins=[PLUGIN],
            tools=_echo_registry(),
            responses=[
                call_tool("codemode", {"script": script}, call_id="cm1"),
                say("done"),
            ],
        )
        reader = host.ctx.subscribe()
        try:
            answer = await host.ctx.agent.chat("run it")
            seen = []
            while (event := reader.take()) is not None:
                seen.append(event)
        finally:
            reader.close()
        return host, answer, seen

    async def test_script_calls_are_program_origin(self, plugin_host):
        script = (
            "outcomes = await asyncio.gather("
            "tools.echo({'value': 'one'}), tools.echo({'value': 'two'}))\n"
            "for o in outcomes:\n"
            "    text(o.content)\n"
            "store('last', [o.content for o in outcomes])"
        )
        host, answer, seen = await self._turn(plugin_host, script)
        assert answer == "done"

        # The script's calls were observable on the channel, as program
        # origin nested under the codemode call — the audit trail exists.
        echo_finished = [
            e
            for e in seen
            if e.type == "tool_call_finished" and e.name == "echo"
        ]
        assert len(echo_finished) == 2
        assert all(e.origin == "program" for e in echo_finished)
        assert all(e.parent_call_id == "cm1" for e in echo_finished)
        assert [e.call_id for e in echo_finished] == ["cm1:1", "cm1:2"]
        echo_started = [
            e for e in seen if e.type == "tool_call_started" and e.name == "echo"
        ]
        assert [e.call_id for e in echo_started] == ["cm1:1", "cm1:2"]

        # codemode's own call is ordinary model origin.
        cm_finished = [
            e
            for e in seen
            if e.type == "tool_call_finished" and e.name == "codemode"
        ]
        assert len(cm_finished) == 1
        assert cm_finished[0].origin == "model"

        # The turn's terminal state counts only the model's call.
        end = terminal(seen)
        assert end.tool_calls_made == 1
        assert host.ctx.agent.tool_call_count == 1

        # messages carry exactly one tool result — codemode's. The echo
        # calls never entered the conversation.
        tool_messages = [
            m for m in host.ctx.agent.messages if m["role"] == "tool"
        ]
        assert len(tool_messages) == 1
        content = str(tool_messages[0]["content"])
        assert content.startswith("Script completed in ")
        assert "echo:one" in content and "echo:two" in content
        assert "cm1:1" not in content  # nested ids stay out of the model's view

        # The store slot holds what the script committed.
        assert host.ctx.plugin_state("codemode") == {"last": ["echo:one", "echo:two"]}

    async def test_failing_script_in_turn(self, plugin_host):
        script = 'text("partial")\nraise ValueError("broken")'
        host, answer, seen = await self._turn(plugin_host, script)
        assert answer == "done"
        cm_finished = [
            e
            for e in seen
            if e.type == "tool_call_finished" and e.name == "codemode"
        ]
        assert cm_finished[0].status == "ok"  # the tool itself ran fine
        tool_messages = [
            m for m in host.ctx.agent.messages if m["role"] == "tool"
        ]
        content = str(tool_messages[0]["content"])
        assert content.startswith("Script failed in ")
        assert "partial" in content
        assert content.endswith(
            'Script error (line 2): ValueError: broken\nraise ValueError("broken")'
        )
        assert host.ctx.plugin_state("codemode") == {}

    async def test_denied_visibility_never_reaches_messages(self, plugin_host):
        # A model-only companion tool is invisible to the script's audience:
        # its denial is a program-origin event, not a message.
        registry = _echo_registry()
        registry.register(
            Tool(
                name="model_only",
                description="not for programs",
                schema={"type": "object", "properties": {}},
                func=lambda args: "nope",
                availability="model",
            )
        )
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            responses=[
                call_tool(
                    "codemode",
                    {"script": "return (await asyncio.gather("
                     "tools.echo({'value': 'a'}), "
                     "tools.model_only({}), "
                     "return_exceptions=True))[1]"},
                    call_id="cm1",
                ),
                say("done"),
            ],
        )
        answer = await host.ctx.agent.chat("go")
        assert answer == "done"
        denied = [
            e
            for e in host.ctx.agent.channel.history()
            if e.type == "tool_call_finished" and e.status == "denied"
        ]
        assert len(denied) == 1 and denied[0].origin == "program"
        assert host.ctx.agent.tool_call_count == 1
        tool_messages = [
            m for m in host.ctx.agent.messages if m["role"] == "tool"
        ]
        assert len(tool_messages) == 1  # only codemode's result, denial included
        assert "denied" in str(tool_messages[0]["content"])

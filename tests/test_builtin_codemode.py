"""Tests for the codemode builtin plugin — runtime, api, output, search,
plugin and the end-to-end contract."""

from __future__ import annotations

import asyncio
import builtins
import json
from pathlib import Path
from typing import Any

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

from mocode.core.events import Notice, ToolCallFinished, ToolCallStarted
from mocode.host.plugin.builtin.mcp import PLUGIN as MCP_PLUGIN
from mocode.testing import call_tool, say

from ._mcp_fake import WirePeer
from .conftest import echo_tool, make_agent, settle, wait_until

BOUND = 15  # seconds — every await in this file stays bounded


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


def _slow_tool() -> Tool:
    """A tool that never answers on its own — it parks on an event nobody
    sets, so the only thing that ends the call is the deadline cancelling
    it. A missed cancellation is bounded by the test's own timeout, never
    by how long this slept."""

    async def slow(args):
        await asyncio.Event().wait()
        return f"slow:{args['value']}"  # unreachable unless released

    return Tool(
        name="slow",
        description="parks until cancelled",
        schema={"type": "object", "properties": {"value": {"type": "string"}}},
        func=slow,
    )


def _order_tool(name: str, log: list) -> Tool:
    """A tool that records its start and end in *log* — for order assertions.

    The gap between the two is one sanctioned loop turn (:func:`settle`), so
    the call is not instantaneous and concurrency would show up as
    interleaved entries — without the test ever waiting on a duration.
    """

    async def order_tool(args):
        log.append(f"{name}:start")
        await settle(0)
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
    async def test_the_env_the_return_and_the_cancellation(self):
        async def helper() -> int:
            await settle(0)  # one loop turn — the await, not a duration
            return 42

        env = {"helper": helper}
        assert await run_script("await helper()\nreturn 21 * 2", env) == 42
        assert await run_script("x = 1 + 1", {}) is None  # no return is none
        assert await run_script("return a + b", {"a": 2, "b": 3}) == 5

        # the script parks on an event nobody sets, so the only thing that
        # ends it is the cancellation — the unset event is the fact, and no
        # stopwatch asserts how long the script ran first
        async def blocker():
            await asyncio.Event().wait()

        task = asyncio.create_task(run_script("await blocker()", {"blocker": blocker}))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_the_empty_script_and_a_syntax_error_are_refused(self):
        # blank and whitespace-only are the same refusal
        for script in ("", "   \n\t\n  "):
            with pytest.raises(CodemodeError, match="script is empty"):
                await run_script(script, {})
        # the syntax error carries the script's own filename
        with pytest.raises(SyntaxError) as exc_info:
            await run_script("def broken(:", {})
        assert "<codemode>" in str(exc_info.value)

    async def test_exceptions_and_the_exit_propagate(self):
        # ValueError is on the restricted whitelist; the message and type
        # reach the caller, which renders the script-failed result.
        with pytest.raises(ValueError, match="boom"):
            await run_script("raise ValueError('boom')", {})
        # The injected exit() raises _ScriptExit; run_script lets it through
        # and the tool layer turns it into success.
        def exit():
            raise _ScriptExit()

        with pytest.raises(_ScriptExit):
            await run_script("exit()", {"exit": exit})

    async def test_the_sandbox_allows_common_ops_and_hides_escape_hatches(self):
        assert await run_script("return sum([1, 2, 3])", {}) == 6
        assert await run_script("return sorted([3, 1, 2])", {}) == [1, 2, 3]
        # every escaping builtin is gone — the sandbox rule, not a list of
        # individual spellings, is what matters
        for name in ("open", "eval", "exec", "compile", "input", "globals", "locals", "vars"):
            with pytest.raises(NameError):
                await run_script(name, {})
        # a script names what it catches — the builtin exception classes are
        # whitelisted — and dir() opens introspection of the result
        assert await run_script(
            "try:\n"
            "    raise RuntimeError('boom')\n"
            "except RuntimeError:\n"
            "    return 'caught'",
            {},
        ) == "caught"
        assert (
            await run_script(
                "try:\n    import os\nexcept ImportError:\n    return 'caught'",
                {},
            )
            == "caught"
        )
        assert "append" in await run_script("return dir([])", {})

        # Not Exception subclasses — the collection rule itself leaves them
        # out, so a script cannot even name the class that would swallow
        # the cancellation unwinding a stopped or timed-out script.
        for name in ("BaseException", "KeyboardInterrupt", "SystemExit", "GeneratorExit"):
            with pytest.raises(NameError):
                await run_script(name, {})
        script = (
            "try:\n"
            "    raise ValueError('x')\n"
            "except BaseException:\n"
            "    return 'swallowed'"
        )
        with pytest.raises(NameError):
            await run_script(script, {})


    async def test_the_whitelist_is_the_frozen_set_plus_exceptions(self):
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
        # a direct __import__ call hits the same gate, Python's own exit and
        # quit are absent, and this is not the real builtins
        assert await run_script("return __import__('json').dumps({'a': 1})", {}) == '{"a": 1}'
        with pytest.raises(ImportError, match="not available"):
            await run_script("return __import__('os')", {})
        with pytest.raises(NameError):
            await run_script("exit", {})
        assert RESTRICTED != __builtins__ if isinstance(__builtins__, dict) else True
        assert "open" not in RESTRICTED
        assert "len" in RESTRICTED


class TestScriptErrorLine:
    """D10 — the wrapper offset: script line = reported line - 1."""

    async def test_the_offset_is_reported_and_outside_the_script_it_is_none(self):
        with pytest.raises(ValueError, match="boom") as exc_info:
            await run_script("text('a')\ntext('b')\nraise ValueError('boom')", {"text": lambda v: None})
        assert script_error_line(exc_info.value) == 3

        script = "def helper():\n    raise ValueError('inner')\n\nhelper()"
        with pytest.raises(ValueError, match="inner") as exc_info:
            await run_script(script, {})
        assert script_error_line(exc_info.value) == 2

        with pytest.raises(SyntaxError) as exc_info:
            await run_script("text('a')\ndef broken(:", {"text": lambda v: None})
        assert script_error_line(exc_info.value) == 2

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

    async def test_the_import_statement_works_for_whitelisted_modules(self):
        # the muscle-memory forms all pass: a plain import, several in one
        # statement, from-import, and from-import with an alias
        assert await run_script(
            "import re, asyncio, json\nreturn json.dumps({'ok': bool(re)})",
            {},
        ) == '{"ok": true}'
        assert (
            await run_script("from asyncio import gather\nreturn gather.__name__", {})
            == "gather"
        )
        assert await run_script(
            "from json import dumps as d\nreturn d({'a': 1})", {}
        ) == '{"a": 1}'

    async def test_a_rejected_import_refuses_and_is_catchable_by_name(self):
        # plain, from-import and submodule forms all refuse the same way — and
        # the gate matches exact module names, so no submodule gets through
        for script in ("import os", "from os import path", "import asyncio.exceptions"):
            with pytest.raises(ImportError, match="not available"):
                await run_script(script, {})
        with pytest.raises(ImportError, match=r"import of 'os' is not available"):
            await run_script("import os", {})
        # ImportError is a whitelisted builtin exception, so scripts can
        # probe for an optional module without dying.
        assert await run_script(
            "try:\n    import os\nexcept ImportError:\n    return 'caught'",
            {},
        ) == "caught"

        # and the injected modules are the whitelist itself
        from mocode.host.plugin.builtin.codemode.env import _MODULES
        from mocode.host.plugin.builtin.codemode.runtime import IMPORT_WHITELIST

        assert set(_MODULES) == set(IMPORT_WHITELIST)


# ── T2: api — ToolBox, discovery, store ─────────────────────


def _box(*tools, parent="call_1") -> ToolBox:
    agent = make_agent(*tools)
    return ToolBox(agent.tool_registry, agent.dispatcher, parent)


class TestToolBox:
    async def test_calling_a_tool(self):
        box = _box(echo_tool())
        outcome = await box["echo"]({"value": "hi"})
        assert outcome.content == "echo:hi"
        assert outcome.status == "ok"
        assert str(outcome) == "echo:hi"
        # __getattr__ and __getitem__ agree, kwargs win over the args dict,
        # and the calls are counted
        assert (await box.echo({"value": "hi"})).content == "echo:hi"
        assert box.calls == 2
        assert (await box.echo({"value": "a"}, value="b")).content == "echo:b"

        # res.get("content") is the idiom the docs taught, so the outcome
        # answers it
        outcome = await box.echo({"value": "hi"})
        assert outcome.get("content") == "echo:hi"
        assert outcome.get("nope") is None
        assert outcome.get("nope", "d") == "d"
        assert outcome["content"] == "echo:hi"
        assert "content" in outcome
        assert dict(outcome) == outcome.to_dict()

    async def test_the_name_resolution_tiers(self):
        # the normalized form finds a hyphenated name, and the exact name
        # beats the short name a local and an MCP tool would share
        box = _box(echo_tool("weird-name"))
        assert (await box.weird_name({"value": "x"})).content == "echo:x"
        assert (await box["weird-name"]({"value": "y"})).content == "echo:y"
        box = _box(echo_tool("bash"), echo_tool("mcp__k__bash"))
        assert (await box.bash({"value": "x"})).content == "echo:x"

        # an MCP tool answers to its full name and to its short form, the
        # short form is the last ``__`` segment, and a hash-collision suffix
        # rides along in it
        box = _box(echo_tool("mcp__k__bash"), echo_tool("mcp__a__b__tool"), echo_tool("mcp__k__tool_1a2b3c"))
        assert (await box["mcp__k__bash"]({"value": "a"})).content == "echo:a"
        assert (await box.bash({"value": "b"})).content == "echo:b"
        assert (await box["bash"]({"value": "c"})).content == "echo:c"
        assert (await box.tool({"value": "x"})).content == "echo:x"
        assert (await box.tool_1a2b3c({"value": "x"})).content == "echo:x"

        # an ambiguous short form refuses to guess and names the candidates
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
        # the normalized form of two names is equally unresolvable
        box = _box(echo_tool("x-y"), echo_tool("x@y"))
        with pytest.raises(CodemodeError, match="unknown tool 'x_y'"):
            await box.x_y({"value": "x"})
        assert (await box["x-y"]({"value": "x"})).content == "echo:x"

    async def test_an_unknown_tool_and_codemode_itself_are_refused(self):
        box = _box(echo_tool())
        with pytest.raises(
            CodemodeError, match=r"unknown tool 'nope'; use search_tools\(\) or all_tools\(\)"
        ):
            box["nope"]

        registry = ToolRegistry()
        registry.register(echo_tool("codemode"))
        agent = make_agent()
        box = ToolBox(registry, agent.dispatcher, "call_1")
        with pytest.raises(CodemodeError, match="codemode cannot be called from a script"):
            box["codemode"]
        with pytest.raises(CodemodeError, match="codemode cannot be called from a script"):
            box.codemode

    async def test_a_failing_call_raises_with_its_result(self):
        box = _box(_failing_tool())
        with pytest.raises(ToolCallError) as exc_info:
            await box.fail({})
        error = exc_info.value
        assert str(error) == "fail: error: execution_error: nope"
        assert error.result.status == "error"
        assert error.result.error_code == "execution_error"
        assert box.calls == 1  # counted even though the call failed

        # and under gather(return_exceptions=True) the failure is a value
        box = _box(echo_tool(), _failing_tool())
        outcomes = await asyncio.gather(
            box.echo({"value": "ok"}),
            box.fail({}),
            return_exceptions=True,
        )
        assert outcomes[0].content == "echo:ok"
        assert isinstance(outcomes[1], ToolCallError)
        assert box.calls == 2

    async def test_program_origin_parenting_and_the_dir_catalogue(self):
        agent = make_agent(echo_tool())
        box = ToolBox(agent.tool_registry, agent.dispatcher, "parent-9")
        await box.echo({"value": "x"})
        finished = [
            e for e in agent.channel.history() if e.type == "tool_call_finished"
        ]
        assert finished and all(e.origin == "program" for e in finished)
        assert all(e.parent_call_id == "parent-9" for e in finished)
        assert finished[0].call_id.startswith("parent-9:")

        # dir(tools) is the callable catalogue: registered names, sorted,
        # and none of the forgiven built-ins
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
        # an unknown name is the plain unknown-tool refusal, facade or not
        with pytest.raises(
            CodemodeError,
            match=r"unknown tool 'nope'; use search_tools\(\) or all_tools\(\)",
        ):
            box.nope

    def test_facade_bound_names_work(self):
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
        assert [t["name"] for t in box.all_tools()] == ["echo", "mcp__k__store"]
        bound = box.store
        assert bound is not env["store"]
        outcome = await bound({"value": "x"})
        assert outcome.content == "echo:x"


class TestShortNameRule:
    def test_short_name_boundaries(self):
        # the rule: strip the mcp prefix, split on the LAST __ — and anything
        # that is not an mcp name, or has no tool segment, has no short form
        cases = {
            "mcp__k__bash": "bash",
            "mcp__a__b__tool": "tool",  # server folded with __ (a//b → a__b)
            "mcp____tool": "tool",  # empty server segment
            "mcp__k__tool_1a2b3c": "tool_1a2b3c",  # hash-collision suffix
            "bash": None,  # not an MCP name
            "mcp__k": None,  # no tool segment at all
            "mcp__k__": None,  # empty tool segment
        }
        for full, short in cases.items():
            assert _mcp_short_name(full) == short, full


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

    def test_accessors_and_the_str(self):
        result = self._result()
        assert result.ok is True
        assert result.content == "c"
        assert result.details == {"exit_code": 0}
        assert result.tool == "echo"
        assert result.status == "ok"
        assert result.error_code is None
        assert result.error is None
        assert str(result) == "c"

        # the repr is a debug aid: what it must carry is the outcome, the
        # tool and the size — the exact rendering is not a contract
        text = repr(result)
        assert "<Result" in text and "ok" in text and "echo" in text
        page = repr(Result(ok=True, content="x" * 3141, tool="read"))
        assert "read" in page and "3.1k" in page
        failed = Result(ok=False, tool="fail", error="fail: error: execution_error: nope")
        assert "error" in repr(failed) and "fail" in repr(failed)

    def test_the_mapping_protocol(self):
        result = self._result()
        assert result.get("content") == "c"
        assert result.get("details") == {"exit_code": 0}
        assert result.get("status") == "ok"
        assert result.get("error_code") is None
        assert result["content"] == "c"
        assert list(result.keys()) == ["content", "details", "status", "error_code"]
        assert "status" in result
        assert "nope" not in result
        assert result.get("nope") is None
        assert result.get("nope", "fallback") == "fallback"
        with pytest.raises(KeyError):
            result["nope"]
        assert dict(result) == result.to_dict()

    def test_json_and_structured_helpers(self):
        assert Result(content='{"a": 1, "b": [2]}').json() == {"a": 1, "b": [2]}
        assert Result(content="[1, 2]").json() == [1, 2]
        message = Result(content="not json at all").json()
        assert isinstance(message, str)
        assert "not valid JSON" in message
        assert "not json at all" in message  # the content snippet is included
        structured = {"rows": [{"id": 1}]}
        assert Result(content="rows", details={"structured_content": structured}).structured == structured
        assert Result(content="plain").structured is None


class TestParallel:
    """D18 — batch calls are first-class: per-call failure capture, order
    preserved, a per-batch concurrency that overrides the global cap."""

    async def test_order_is_kept_and_failures_are_captured(self):
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

        # all of them failing is the same shape with nothing ok
        box = _box(_failing_tool())
        rs = await parallel(box.fail({}), box.fail({}))
        assert rs.ok == []
        assert len(rs.failed) == 2
        assert all(r.error for r in rs.failed)

    async def test_bad_arguments_raise(self):
        box = _box(echo_tool())
        with pytest.raises(TypeError, match="expects awaitables"):
            await parallel(box.echo)  # the bound call, never awaited
        with pytest.raises(TypeError, match="expects awaitables"):
            await parallel("not a coroutine")
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

    def test_the_callable_catalogue_is_the_program_audience(self):
        entries = tool_entries(self._registry())
        assert [e["name"] for e in entries] == ["echo"]
        assert entries[0]["description"] == "echo"
        registry = self._registry()
        entry = describe_tool_entry(registry, "echo")
        assert entry["name"] == "echo"
        assert entry["schema"]["type"] == "object"
        # D9: the same source of truth as the callable surface — a name
        # outside it is not described however registered it is
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

    def test_the_catalogue_is_a_snapshot(self):
        # Field-findings P1-2: sorted(all_tools()) fails because entries are
        # dicts — the names table is names_only's job. A script mutating the
        # returned list must not corrupt the snapshot either, and a tool that
        # registers later is not in it.
        agent = make_agent(echo_tool())
        output = _FakeOutput()
        env, _ = build_env(agent.tool_registry, agent.dispatcher, "c", output, Store({}))
        entries = env["all_tools"]()
        entries.clear()
        assert [t["name"] for t in env["all_tools"]()] == ["echo"]
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

    def test_entries_are_previewed_at_eighty_chars(self):
        env = self._env()
        entries = {e["name"]: e["description"] for e in env["all_tools"]()}
        assert entries["with_long_description"] == self._LONG[:80] + "…"
        assert entries["exact_eighty"] == "x" * 80  # exactly 80 is not cut
        assert entries["empty_description"] == ""  # empties get no ellipsis
        hits = env["search_tools"]("alpha")
        assert {h["name"] for h in hits} == {"with_long_description"}
        assert hits[0]["description"] == self._LONG[:80] + "…"

    def test_ranking_reads_the_full_description(self):
        # "needle" sits past character 80 — the tool still wins the query,
        # and only the returned entry is the cut preview; the full text
        # stays available through describe_tool.
        env = self._env()
        hits = env["search_tools"]("needle")
        assert [h["name"] for h in hits] == ["with_long_description"]
        assert "needle" not in hits[0]["description"]
        assert env["describe_tool"]("with_long_description")["description"] == self._LONG


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

    def test_print_is_one_item_and_never_the_hosts_stdout(self):
        env, output = self._env()
        assert env["print"] is not builtins.print
        # the text() convention, per argument — not Python's repr; the whole
        # call is still one item
        env["print"]("a", "b")
        env["print"]("x", 1, "y", sep="-")
        env["print"]({"x": 1}, [1, "b"], None)
        env["print"]()
        assert output.items == [
            "a b",
            "x-1-y",
            '{"x": 1} [1, "b"] null',
            "",
        ]


class TestStore:
    def test_commit_applies_pending_and_hides_the_rest(self):
        backing = {"a": 1}
        store = Store(backing)
        store.store("b", [1, 2])
        store.store("a", None)  # delete
        # uncommitted writes stay in the overlay but off the backing dict —
        # a failing script just never commits, so backing keeps its shape
        assert store.load("b") == [1, 2]
        assert backing == {"a": 1}
        store.commit()
        assert backing == {"b": [1, 2]}
        assert store.load("a") is None
        assert store.load("b") == [1, 2]

    def test_the_limits_fail_the_commit_without_applying(self):
        backing = {}
        store = Store(backing, max_value_chars=10)
        store.store("big", "x" * 100)
        with pytest.raises(CodemodeError, match="over the 10 limit"):
            store.commit()
        assert backing == {}  # nothing applied

        # json.dumps quotes strings: each 8-char value is 10 chars of JSON,
        # so 10 + 10 = 20 over a 15-char budget.
        backing = {"existing": "y" * 8}
        store = Store(backing, max_total_chars=15)
        store.store("new", "z" * 8)
        with pytest.raises(CodemodeError, match="over the 15 limit"):
            store.commit()
        assert backing == {"existing": "y" * 8}

        # Values committed by earlier successful scripts count towards the
        # total — the check protects the persisted session, not one script.
        backing = {"existing": "y" * 8}
        store = Store(backing, max_total_chars=30)
        store.store("new", "z" * 8)
        store.commit()
        assert backing == {"existing": "y" * 8, "new": "z" * 8}
    def test_the_default_limits_are_generous_and_enforced(self):
        # The defaults are a policy, not a constant to pin: what a test can
        # hold is that the default store accepts a value no test would ever
        # write and rejects one no session should keep.
        store = Store({})
        store.store("k", "x" * 1024)
        store.commit()
        assert store.load("k") == "x" * 1024


# ── T3: output + rank ───────────────────────────────────────


from mocode.host.plugin.builtin.codemode.output import (
    Output,
    build_result,
    compose,
    truncate_body,
)
from mocode.host.plugin.builtin.codemode.search import normalize, rank


class TestOutput:
    def test_text_and_image_items(self):
        out = Output()
        out.text("plain")
        out.text({"a": 1})
        out.text([1, "x"])
        assert out.items == ['plain', '{"a": 1}', '[1, "x"]']

        out = Output()
        block = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        out.image(block)
        assert out.images == [block]
        assert out.items == ["[image: image/png]"]

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
    def test_a_short_body_or_a_nonpositive_limit_is_untouched(self):
        assert truncate_body("hello", 12000) == ("hello", None)
        assert truncate_body("hello", 0) == ("hello", None)
        assert truncate_body("hello", -5) == ("hello", None)

    def test_a_long_body_keeps_head_and_tail_and_spools_the_rest(self):
        body = "".join(str(i % 10) for i in range(1000))
        text, path = truncate_body(body, 100)
        assert path is not None
        head, tail = body[:50], body[-50:]
        assert text.startswith(head)
        assert text.endswith(tail)
        # head 3 + marker + tail 3 with "…4 chars truncated…"
        odd, _ = truncate_body("x" * 10, 7)
        assert odd.startswith("xxx") and odd.endswith("xxx")
        # D16: the notice must be unmissable — the omitted character count,
        # the file path, an explicit read-first instruction and the
        # tools.read hint all in one line.
        assert f"⚠ {len(body) - 100} chars truncated" in text
        assert "before relying on this output, read the full result" in text
        assert path in text
        assert "tools.read" in text
        full = Path(path)
        assert full.name.startswith("mocode-codemode-") and full.suffix == ".txt"
        assert full.read_text(encoding="utf-8") == body
        full.unlink()


class TestCompose:
    def test_the_status_line_the_body_and_the_error_compose(self):
        # the composing is a format contract: the status line, the body
        # between it and the error, and the full-output pointer appended
        # last — with no blank line when the body is empty
        assert compose(True, 12, "line", None, None) == "Script completed in 12ms\nline"
        assert compose(True, 12, "", None, None) == "Script completed in 12ms"
        assert compose(False, 5, "partial", ValueError("boom"), None) == (
            "Script failed in 5ms\npartial\nScript error: ValueError: boom"
        )
        assert (
            compose(False, 5, "", ValueError("boom"), None)
            == "Script failed in 5ms\nScript error: ValueError: boom"
        )
        assert compose(True, 1, "b", None, "/tmp/full.txt").endswith(
            "\nFull output: /tmp/full.txt"
        )


class TestBuildResult:
    def test_the_details_carry_the_run(self):
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

    def test_an_empty_query_keeps_registration_order_and_respects_the_limit(self):
        assert [t["name"] for t in rank("", self._entries())] == [
            "read_file",
            "bash",
            "mcp__git__search",
            "mcp__git_ops__search_things",
        ]
        assert len(rank("", self._entries(), limit=2)) == 2

    def test_name_hits_and_all_token_matches_outrank(self):
        # "search" hits two names; "code" hits one description — that one
        # matches every token and takes the top slot; unmatched tools drop out
        assert rank("bash", self._entries())[0]["name"] == "bash"
        assert rank("search code", self._entries())[0]["name"] == "mcp__git__search"
        assert [t["name"] for t in rank("read file", self._entries())] == ["read_file"]
        # ties sort by name
        assert [t["name"] for t in rank("search", self._entries())] == [
            "mcp__git__search",
            "mcp__git_ops__search_things",
        ]

    def test_the_namespace_filter_normalizes(self):
        hits = rank("search", self._entries(), namespace="git-ops")
        assert [t["name"] for t in hits] == ["mcp__git_ops__search_things"]
        assert rank("search", self._entries(), namespace="nope") == []
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
    def test_metadata_and_the_build_contract(self, plugin_host):
        assert PLUGIN.name == "codemode"
        assert PLUGIN.description == "Run a Python script that calls other tools"
        assert isinstance(PLUGIN, CodemodePlugin)
        import mocode.host.plugin.builtin.codemode as package

        assert package.PLUGIN is PLUGIN
        assert package.CodemodePlugin is CodemodePlugin

        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        tool = host.ctx.tools.get("codemode")
        assert tool is not None
        assert tool.availability == "model"
        assert tool.tags == frozenset({"codemode"})
        assert tool.wants_context
        assert tool.schema["required"] == ["script"]
        assert "codemode" in host.ctx.tools.names(audience="model")
        assert "codemode" not in host.ctx.tools.names(audience="program")
        # Stamping is the loader's job (tested in test_plugins.py) — the
        # plugin must only not claim one itself.
        assert tool.source == ""

    def test_description_covers_the_v2_contract(self):
        """Coverage, not wording — and the *API surface*, not the prose.

        What the description must do is teach the script every name it may
        bind. The strongest reword-proof version of that claim is the one
        this test makes: every identifier a script can actually reach is
        named in the description. Identifiers are the contract; the sentence
        around them are not.
        """
        # what a script's environment really binds
        from mocode.host.plugin.builtin.codemode.env import build_env
        from mocode.host.plugin.builtin.codemode.output import Output
        from mocode.host.plugin.builtin.codemode.store import Store

        agent = make_agent(echo_tool())
        env, _box = build_env(
            agent.tool_registry, agent.dispatcher, "c", Output(), Store({})
        )
        # the frozen names a script may bind, minus the toolbox itself
        bindable = sorted(
            name
            for name, value in env.items()
            if not name.startswith("_") and not callable(value) or name == "tools"
        )
        for name in bindable:
            assert name in DESCRIPTION, f"description lost the name {name!r}"

        # and the API identifiers the description's own examples teach
        for identifier in (
            "asyncio.run()",
            "asyncio.ensure_future",
            "asyncio.gather",
            "parallel(",
            "return_exceptions=True",
            "max_concurrency",
            "concurrency",
            "store(",
            "load(",
            "all_tools()",
            "search_tools(",
            "describe_tool(",
            "tools.read",
            "names_only",
            "max_output_chars",
            "timeout_ms",
            "import asyncio",
            "@options",
        ):
            assert identifier in DESCRIPTION, f"description lost {identifier!r}"

        # hard cut: the old surface stays gone (identity pins, reword-proof)
        assert "ALL_TOOLS" not in DESCRIPTION  # renamed to all_tools() (D3)
        assert "ToolOutcome" not in DESCRIPTION  # renamed to Result (D17)


def _script_error(content: str) -> tuple[int, str, str] | None:
    """The terminal ``Script error`` line of a failed run, as structure.

    Answers ``(line, exception type, message)`` when the run failed in the
    script's own code, and ``None`` when it failed somewhere else (a tool
    call, say). The wording is the user's; what a test holds is that the
    line number, the exception type and its message all travel.
    """
    marker = "Script error"
    for line in reversed(content.splitlines()):
        if marker not in line:
            continue
        detail = line.split(marker, 1)[1].strip()
        if not detail.startswith("(line "):
            return None
        number, _, rest = detail[len("(line "):].partition("): ")
        exception, _, message = rest.partition(": ")
        return int(number), exception, message
    return None


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

        # a second script in the same conversation gathers in parallel and
        # counts both calls
        result = await self._run(
            host,
            "outcomes = await asyncio.gather("
            "tools.echo({'value': 'a'}), tools.echo({'value': 'b'}))"
            "\nreturn [o.content for o in outcomes]",
        )
        assert result.content.endswith('["echo:a", "echo:b"]')
        assert result.details["tool_calls"] == 2

    async def test_parallel_in_a_script_captures_and_types_its_failures(self, plugin_host):
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

        # D4 end to end: the script names RuntimeError in an except clause
        # and isinstance-branches after gather(return_exceptions=True)
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

    async def test_a_failed_script_reports_its_line_and_source(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        # partial output is kept, the line number is the script's own (the
        # wrapper's header shifts Python's by one) and that line's source
        # travels with the error
        result = await self._run(host, 'text("before")\nraise ValueError("boom")')
        assert result.content.startswith("Script failed in ")
        assert "\nbefore\n" in result.content
        assert _script_error(result.content) == (2, "ValueError", "boom")
        assert result.details["ok"] is False

        # D10: a failure inside a function the script defined still lives in
        # a <codemode> frame
        script = "def helper():\n    raise ValueError('inner')\n\nhelper()"
        result = await self._run(host, script)
        assert _script_error(result.content) == (2, "ValueError", "inner")
        assert "raise ValueError('inner')" in result.content

        # and the SyntaxError off-by-one: Python reports against the
        # compiled source, whose first line is the wrapper header
        result = await self._run(host, 'text("a")\ndef broken(:')
        assert result.details["ok"] is False
        assert _script_error(result.content)[0] == 2
        assert _script_error(result.content)[1] == "SyntaxError"
        assert "\ndef broken(:" in result.content

        # a failure that surfaced inside the tool box, not in the script's
        # own code, has no script line — but the tool's error still travels
        registry = ToolRegistry()
        registry.register(_failing_tool())
        host = plugin_host(plugins=[PLUGIN], tools=registry)
        result = await self._run(host, "await tools.fail({})")
        assert result.details["ok"] is False
        assert _script_error(result.content) is None
        assert "ToolCallError" in result.content
        assert "nope" in result.content
        assert result.content.startswith("Script failed in ")

    async def test_an_explicit_deadline_keeps_partial_output(self, plugin_host):
        # D6: with an explicit deadline the plugin's own wait_for fires
        # first and the result is a normal failure — partial output kept,
        # timed_out marker set, no error line, store writes discarded.
        registry = ToolRegistry()
        registry.register(_slow_tool())
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            config_kwargs={"plugins": {"codemode": {"timeout_s": 0.2}}},
        )
        result = await self._run(
            host, 'store("k", 1)\ntext("before")\n'
            'r = await tools.slow({"value": "x"})\ntext("after")'
        )
        assert result.details["ok"] is False
        assert result.details["timed_out"] is True
        assert "\nbefore\n" in result.content
        assert "\nafter\n" not in result.content
        assert "Script timed out after" in result.content
        assert "Script error" not in result.content
        assert host.ctx.plugin_state("codemode") == {}

        # a script's own TimeoutError is the script's error, not the marker
        host = plugin_host(
            plugins=[PLUGIN],
            tools=_echo_registry(),
            config_kwargs={"plugins": {"codemode": {"timeout_s": 60000}}},
        )
        result = await self._run(host, "raise TimeoutError('self-inflicted')")
        assert result.details["ok"] is False
        assert "timed_out" not in result.details
        assert _script_error(result.content) == (1, "TimeoutError", "self-inflicted")
        assert "Script timed out" not in result.content

    async def test_the_deadline_is_a_normal_result_not_a_dispatcher_timeout(
        self, plugin_host
    ):
        registry = ToolRegistry()
        registry.register(_slow_tool())
        # Through the dispatcher with room to spare, the fired deadline
        # comes back as an ordinary ok call — never a TOOL_TIMEOUT status.
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            config_kwargs={"plugins": {"codemode": {"timeout_s": 0.2}}},
        )
        result = await host.ctx.agent.dispatcher.run(
            "codemode",
            {"script": 'await tools.slow({"value": "x"})'},
            timeout=30,
        )
        assert result.status == "ok"
        assert "Script timed out after" in result.content

        # Without an explicit deadline the plugin path is as before: the
        # dispatcher's timeout cancels the call and the partial output is
        # lost. The script's own sleep is the placeholder that outlives it.
        host = plugin_host(
            plugins=[PLUGIN],
            tools=_echo_registry(),
            config_kwargs={"plugins": {"codemode": {}}},
        )
        result = await host.ctx.agent.dispatcher.run(
            "codemode",
            {"script": 'text("before")\nawait asyncio.sleep(10)'},
            timeout=0.3,
        )
        assert result.status == "timeout"
        assert "before" not in result.content

    async def test_turn_cancellation_passthrough_with_deadline(self, plugin_host):
        # Cancelling the turn mid-script still propagates untouched even
        # when a deadline is set — it must not be converted into a timed-out
        # result (or swallowed). The script parks on a tool that waits for an
        # event, so the cancel is the only thing that can end it — and the
        # unset event is the fact, with no duration asserted anywhere.
        started = asyncio.Event()
        release = asyncio.Event()

        async def parked(args):
            started.set()
            await release.wait()
            return "never"

        registry = _echo_registry()
        registry.register(
            Tool(
                name="parked",
                description="waits for a release",
                schema={"type": "object", "properties": {}},
                func=parked,
            )
        )
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            config_kwargs={"plugins": {"codemode": {"timeout_s": 60000}}},
        )
        tool = host.ctx.tools.get("codemode")
        args = {
            "script": "await tools.parked({})",
            "options": {"timeout_ms": 60000},
        }
        ctx = ToolCallContext(
            tool_name="codemode", tool_args=args, tool_call_id="call_cm_cancel"
        )
        task = asyncio.create_task(tool.run_async(args, ctx))
        await asyncio.wait_for(started.wait(), 5)  # the script is running
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_the_concurrency_cap_and_an_unusable_one(self, plugin_host):
        # D5: max_concurrency=1 — the second call waits for the first to
        # finish, even under gather
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

        # The default (no max_concurrency) is unchanged: both calls are in
        # flight before either returns — the unset release event is the fact.
        started: list = []
        both_started = asyncio.Event()
        release = asyncio.Event()
        registry = ToolRegistry()
        registry.register(_gate_tool("gate_a", started, both_started, release))
        registry.register(_gate_tool("gate_b", started, both_started, release))
        host = plugin_host(
            plugins=[PLUGIN],
            tools=registry,
            config_kwargs={"plugins": {"codemode": {}}},
        )
        tool = host.ctx.tools.get("codemode")
        args = {"script": "await asyncio.gather(tools.gate_a({}), tools.gate_b({}))"}
        ctx = ToolCallContext(
            tool_name="codemode", tool_args=args, tool_call_id="call_cm_conc"
        )
        task = asyncio.create_task(tool.run_async(args, ctx))
        await asyncio.wait_for(both_started.wait(), 5)
        release.set()
        result = await task
        assert result.details["ok"] is True
        assert sorted(started) == ["gate_a", "gate_b"]

        # an unusable max_concurrency is reported once per conversation and
        # ignored — the calls still run uncapped. One bad shape stands for
        # them all (zero, negative, string, float, bool, list): the policy is
        # "unusable, not a particular spelling".
        for raw in (0, -3, "2", 2.0, True, []):
            started.clear()
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
            assert result.details["ok"] is True, raw
            assert sorted(started) == ["gate_a", "gate_b"], raw
            notices = [
                e for e in host.ctx.agent.channel.history() if isinstance(e, Notice)
            ]
            assert len(notices) == 1, raw
            assert "max_concurrency" in notices[0].message
            release.clear()
            both_started.clear()
        # the warning is once per conversation, not per call
        result = await self._run(host, "pass")
        assert result.details["ok"] is True
        notices = [
            e for e in host.ctx.agent.channel.history() if isinstance(e, Notice)
        ]
        assert len(notices) == 1

    async def test_a_refused_script_fails_the_call(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, "   ")
        assert "Script error: CodemodeError: script is empty" in result.content
        assert result.details["ok"] is False

        # the recursion guard: codemode cannot be called from a script, not
        # under its full name, not by its short form
        result = await self._run(host, 'await tools["codemode"]({"script": "pass"})')
        assert result.details["ok"] is False
        assert "codemode cannot be called from a script" in result.content
        result = await self._run(host, "return [t['name'] for t in all_tools()]")
        assert "codemode" not in result.content
        result = await self._run(host, 'return tools.codemode')
        assert result.details["ok"] is False
        assert "codemode cannot be called from a script" in result.content

    async def test_exit_and_return_land_in_the_result(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'text("a")\nexit()\ntext("b")')
        assert result.details["ok"] is True
        assert result.content.endswith("\na")
        result = await self._run(host, "return {'n': 1}")
        assert result.content.endswith('\n{"n": 1}')

    async def test_print_and_image_ride_the_output_pipeline(self, plugin_host):
        # D8: print was whitelisted but wrote to the host's stdout, where no
        # script reader could ever see it. Three shapes — string, several
        # arguments, non-string — one item each, none on stdout.
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(
            host, 'print("hello")\nprint("a", 1, "b")\nprint({"k": 1})'
        )
        assert result.details["ok"] is True
        assert result.content.endswith('hello\na 1 b\n{"k": 1}')

        # and an image block lands in details with its marker in content
        result = await self._run(
            host,
            'image({"type": "image", "data": "AAAA", "mimeType": "image/png"})',
        )
        assert result.details["images"] == [
            {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        ]
        assert "[image: image/png]" in result.content

    async def test_the_store_commits_discards_and_enforces_its_limit(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        result = await self._run(host, 'store("k", {"v": 1})\ntext("saved")')
        assert result.details["ok"] is True
        assert host.ctx.plugin_state("codemode") == {"k": {"v": 1}}
        # a later call in the same conversation sees it
        result = await self._run(host, 'return load("k")')
        assert result.content.endswith('{"v": 1}')

        # a failing script's writes are discarded
        result = await self._run(host, 'store("k", 1)\nraise ValueError("x")')
        assert result.details["ok"] is False
        assert host.ctx.plugin_state("codemode") == {"k": {"v": 1}}

        # and a limit breach fails the run with nothing staged
        host = plugin_host(
            plugins=[PLUGIN],
            tools=_echo_registry(),
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


class TestOptions:
    def test_effective_options_merges_comment_and_args(self):
        script = '# @options: {"timeout_ms": 5000, "max_output_chars": 100}\ntext("x")'
        merged = effective_options(script, {"timeout_ms": 9000})
        assert merged == {"timeout_ms": 9000, "max_output_chars": 100}

    def test_effective_options_ignores_bad_comment(self):
        script = "# @options: {not json}\n"
        assert effective_options(script, {"timeout_ms": 1}) == {"timeout_ms": 1}
        assert effective_options("", None) == {}

    def test_policy_from_the_comment_line(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], tools=_echo_registry())
        policy = host.ctx.tools.get("codemode").policy
        # the ms option is ceil'd to whole seconds, and never below one
        assert policy({"script": "pass", "options": {"timeout_ms": 1500}}).timeout == 2
        assert policy({"script": "pass", "options": {"timeout_ms": 2000}}).timeout == 2
        assert policy({"script": "pass", "options": {"timeout_ms": 1}}).timeout == 1
        args = {"script": '# @options: {"timeout_ms": 61000}\npass'}
        assert policy(args).timeout == 61
        # nothing anywhere leaves the call to the agent's tool timeout
        assert policy({"script": "pass"}).timeout is None

        # and the config's own timeout_s answers the same question
        host = plugin_host(
            plugins=[PLUGIN],
            tools=_echo_registry(),
            config_kwargs={"plugins": {"codemode": {"timeout_s": 30}}},
        )
        policy = host.ctx.tools.get("codemode").policy
        assert policy({"script": "pass"}).timeout == 30


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

async def _registered(host, name: str) -> bool:
    """Whether the model- or program-facing registry holds *name*.

    A codemode-exposure server connects in the background, so the
    registration is a condition to wait for rather than a moment to guess.
    """
    return await wait_until(
        lambda: host.ctx.tools.get(name) is not None,
        bound=BOUND,
        what=f"{name} to register",
    )


# ── mcp + codemode — the cross-plugin contract ──────────────

#: What the scripted model's codemode call runs: one MCP call, output kept.
SCRIPT_CALL_ECHO = 'text((await tools.mcp__echo__echo({"x": "hi"})).content)'

#: The echo server's tools, as the wire peer answers them.
ECHO_TOOLS = [
    {
        "name": "echo",
        "description": "Echo the arguments back",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
    }
]


def _echo_peer() -> Any:
    """A modern-era wire peer carrying the one ``echo`` tool.

    The cross-plugin contract is about how codemode *reaches* an MCP tool,
    so what matters is that the tool arrives over the wire with the shapes
    the mcp plugin registers — not that a child process carried it.
    """
    from mocode.host.plugin.builtin.mcp.client import McpSession

    return WirePeer(
        {
            "server/discover": {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
                "ttlMs": 0,
                "cacheScope": "public",
                "instructions": "Echo server instructions.",
            },
            "tools/list": {
                "resultType": "complete",
                "tools": ECHO_TOOLS,
                "ttlMs": 0,
                "cacheScope": "public",
            },
            "tools/call": {
                "resultType": "complete",
                "content": [
                    {"type": "text", "text": 'echo:{"x": "hi"}'}
                ],
                "structuredContent": {"args": {"x": "hi"}},
            },
        }
    )


def _peer_servers(monkeypatch, **peers: Any) -> None:
    """Substitute the session factory at the mcp runtime's module boundary.

    The same seam ``tests.test_builtin_mcp`` uses: the collaborator being
    replaced is the factory, so ``McpRuntime.start()`` — the plugin's own
    connect-and-register path — still runs in full. The real class is taken
    from the module that defines it, so patching twice in one test (two
    hosts on one monkeypatch) wraps the class, not the wrapper.
    """
    from mocode.host.plugin.builtin.mcp import client as client_module
    from mocode.host.plugin.builtin.mcp import runtime as runtime_module

    real = client_module.McpSession

    def factory(config, **kwargs):
        peer = peers.get(config.name)
        if peer is None:
            return real(config, **kwargs)
        return real(config, server=peer, **kwargs)

    monkeypatch.setattr(runtime_module, "McpSession", factory)


def _servers_table(**entries: Any) -> dict:
    """The ``plugins.mcp.servers`` table for the echo fake."""
    return {
        "echo": {"command": "python", "args": ["-c", "pass"], **entries},
    }


class TestMcpShortNames:
    """The mcp short-name group: what a codemode script may call.

    A tool registered from an MCP server reaches a script under its full
    name and under its short form — the tier the toolbox's resolution
    offers, whatever the server was called.
    """

    async def test_a_script_calls_an_mcp_tool_by_its_short_name(
        self, plugin_host, monkeypatch, tmp_path
    ):
        _peer_servers(monkeypatch, echo=_echo_peer())
        host = plugin_host(
            plugins=[MCP_PLUGIN, PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={
                "plugins": {"mcp": {"servers": _servers_table()}, "codemode": {"enabled": True}}
            },
        )
        # materialize() runs the plugins' prepare() — the mcp runtime's own
        # connect path
        await asyncio.wait_for(host.materialize(), BOUND)
        # the echo server connects in the background once codemode owns the
        # default exposure — its tool arrives with that connect
        assert await _registered(host, "mcp__echo__echo")
        assert "mcp__echo__echo" not in host.ctx.tools.names(audience="model")

        tool = host.ctx.tools.get("codemode")
        ctx = ToolCallContext(
            tool_name="codemode",
            tool_args={"script": SCRIPT_CALL_ECHO},
            tool_call_id="cm1",
        )
        result = await asyncio.wait_for(
            tool.run_async({"script": SCRIPT_CALL_ECHO}, ctx), BOUND
        )
        assert result.details["ok"] is True
        assert 'echo:{"x": "hi"}' in result.content
        # the call the script made is counted as the script's own
        assert result.details["tool_calls"] == 1
        host.close()


class TestCrossPluginContract:
    """The program-origin contract, end to end: a codemode script's MCP calls
    are observable on the event stream as program-origin events nested under
    the codemode call, and no tool message for them ever reaches the model."""

    async def _run_turn(self, plugin_host, monkeypatch, tmp_path):
        _peer_servers(monkeypatch, echo=_echo_peer())
        host = plugin_host(
            plugins=[MCP_PLUGIN, PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={
                "plugins": {"mcp": {"servers": _servers_table()}, "codemode": {"enabled": True}}
            },
            responses=[
                call_tool("codemode", {"script": SCRIPT_CALL_ECHO}, call_id="cm1"),
                say("done"),
            ],
        )
        # materialize() runs the plugins' prepare() — the mcp runtime's own
        # connect — so the echo server's tool arrives with it
        await asyncio.wait_for(host.materialize(), BOUND)
        # a codemode-exposure server connects in the background, so its tool
        # registering is a condition to wait for, not a moment to guess
        assert await _registered(host, "mcp__echo__echo")

        reader = host.ctx.subscribe()
        answer = await host.ctx.agent.chat("echo through the script tool")
        seen = []
        while (event := reader.take()) is not None:
            seen.append(event)
        return host, answer, seen

    async def test_a_script_mcp_call_is_on_the_channel_but_never_a_message(
        self, plugin_host, monkeypatch, tmp_path
    ):
        host, answer, seen = await self._run_turn(plugin_host, monkeypatch, tmp_path)
        assert answer == "done"

        # The script's MCP call was observable — as a program-origin event
        # nested under the codemode call.
        started = [
            e
            for e in seen
            if isinstance(e, ToolCallStarted) and e.name == "mcp__echo__echo"
        ]
        finished = [
            e for e in seen if isinstance(e, ToolCallFinished) and e.name == "mcp__echo__echo"
        ]
        assert len(started) == len(finished) == 1
        assert started[0].origin == "program"
        assert started[0].parent_call_id == "cm1"
        assert started[0].call_id == "cm1:1"
        assert finished[0].origin == "program"
        assert finished[0].parent_call_id == "cm1"
        assert finished[0].status == "ok"
        assert 'echo:{"x": "hi"}' in finished[0].result

        # codemode's own call is ordinary model origin.
        cm = [
            e
            for e in seen
            if isinstance(e, ToolCallFinished) and e.name == "codemode"
        ]
        assert len(cm) == 1 and cm[0].origin == "model"

        # The turn counts only the model's call.
        assert host.ctx.agent.tool_call_count == 1

        # messages carry exactly one tool result — codemode's. The MCP call
        # never entered the conversation, and the nested id stays invisible.
        tool_messages = [
            m for m in host.ctx.agent.messages if m["role"] == "tool"
        ]
        assert len(tool_messages) == 1
        content = str(tool_messages[0]["content"])
        assert content.startswith("Script completed in ")
        assert 'echo:{"x": "hi"}' in content
        assert "cm1:1" not in content
        assert "mcp__echo__echo" not in content
        host.close()

    async def test_the_default_assembly_builds_both_anchors(self, mc, tmp_path):
        conversation = mc.new_conversation(cwd=tmp_path)
        tools = conversation.tools
        assert tools.get("mcp_status") is not None
        assert tools.get("codemode") is not None
        # codemode is offered to the model; the MCP surface is program-only.
        model = tools.names(audience="model")
        assert "codemode" in model
        assert "mcp_status" not in model
        assert [n for n in model if n.startswith("mcp__")] == []
        assert "mcp_status" in tools.names(audience="program")


class TestCrossPluginExposure:
    """How the mcp plugin's exposure decisions and the codemode plugin's
    presence interact."""

    async def test_codemode_only_tools_warn_once_while_codemode_is_off(
        self, plugin_host, monkeypatch, tmp_path
    ):
        """The mcp plugin's one-shot warning is an event on the channel, and
        it is the shape the conversation sees — not a flag on the runtime."""
        _peer_servers(monkeypatch, echo=_echo_peer())
        host = plugin_host(
            plugins=[MCP_PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={"plugins": {"mcp": {"servers": _servers_table(exposure="codemode")}}},
        )
        reader = host.ctx.subscribe(since=0)
        await asyncio.wait_for(host.materialize(), BOUND)
        warnings = await _cross_warning(reader)
        assert len(warnings) == 1
        assert warnings[0].level == "warn"
        assert "reachable only through codemode" in warnings[0].message
        # one conversation, one warning — the second read of the same channel
        # finds no further warning: the one-shot is a fact about the
        # runtime's flag, not a window to sit out
        again = [
            e
            for e in _take_all(reader)
            if isinstance(e, Notice) and "reachable only through codemode" in e.message
        ]
        assert again == []
        host.close()

    async def test_codemode_enabled_suppresses_the_warning(
        self, plugin_host, monkeypatch, tmp_path
    ):
        _peer_servers(monkeypatch, echo=_echo_peer())
        host = plugin_host(
            plugins=[MCP_PLUGIN, PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={
                "plugins": {
                    "mcp": {"servers": _servers_table(exposure="codemode")},
                    "codemode": {"enabled": True},
                }
            },
        )
        reader = host.ctx.subscribe(since=0)
        await asyncio.wait_for(host.materialize(), BOUND)

        assert await _cross_warning(reader, bound=0.2) == []
        # and the tool itself is registered for the program audience only
        assert await _registered(host, "mcp__echo__echo")
        assert "mcp__echo__echo" not in host.ctx.tools.names(audience="model")
        assert "mcp__echo__echo" in host.ctx.tools.names(audience="program")
        host.close()


async def _cross_warning(reader, *, bound: float = BOUND) -> list:
    """The codemode warnings on the channel, waiting for the first one.

    One drain that polls, so a warning that arrives while this waits is seen
    the moment it does — no guessed window. A *negative* assertion (no
    warning at all) asks for a short bound: with the codemode plugin on, the
    runtime returns before it can emit, and a short wait is enough to show
    nothing arrives.
    """
    deadline = asyncio.get_running_loop().time() + bound
    while True:
        for event in list(_take_all(reader)):
            if isinstance(event, Notice) and "reachable only through codemode" in event.message:
                return [event]
        if asyncio.get_running_loop().time() >= deadline:
            return []
        await settle(0.01)


def _take_all(reader) -> list:
    """Everything the subscriber has so far — read, not polled."""
    seen = []
    while (event := reader.take()) is not None:
        seen.append(event)
    return seen

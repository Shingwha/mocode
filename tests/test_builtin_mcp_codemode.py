"""mcp + codemode together — the cross-plugin contract, end to end.

Both plugins run through the default assembly (``make_mc``,
``plugin_dirs=[]``) rather than a hand-built host: the fake MCP server is a
``tmp_path`` Python script launched with ``sys.executable`` (the W1a pattern,
factored into ``tests/_mcp_fake.py``), and the model is scripted. The core
case proves the program-origin contract: a codemode script's MCP calls are
observable on the event stream as program-origin events, but no tool message
for them ever enters ``conv.messages``.

Every async step is bounded so a broken fake server can never hang the suite
on Windows, where only the direct child process is killed.
"""

from __future__ import annotations

import asyncio

from mocode.core.events import Notice, ToolCallFinished, ToolCallStarted
from mocode.host.config import Config
from mocode.host.plugin.builtin.mcp.session import STATE_CLOSED
from mocode.testing import call_tool, say

from ._mcp_fake import stdio_entry, write_mcp_json, write_server
from .conftest import make_config, project, wire

BOUND = 15  # seconds — every await in this file stays bounded
POLL = 0.05
POLL_ATTEMPTS = 200  # 200 * 0.05s = 10s

#: What the scripted model's codemode call runs: one MCP call, output kept.
SCRIPT_CALL_ECHO = 'text((await tools.mcp__echo__echo({"x": "hi"})).content)'


def _config(plugins: dict) -> Config:
    """The standard test config plus a plugins block."""
    config = make_config()
    config.plugins = plugins
    return config


def _drain(reader) -> list:
    seen = []
    while (event := reader.take()) is not None:
        seen.append(event)
    return seen


async def _wait_for(predicate, what: str) -> None:
    for _ in range(POLL_ATTEMPTS):
        if predicate():
            return
        await asyncio.sleep(POLL)
    raise AssertionError(f"timed out waiting for {what}")


def _echo_project(tmp_path, name: str, **server_extra):
    """A project directory whose .mocode/mcp.json serves the echo fake."""
    proj = project(tmp_path, name)
    script = write_server(tmp_path, f"{name}_server.py")
    write_mcp_json(
        proj / ".mocode" / "mcp.json",
        {"echo": stdio_entry(script, **server_extra)},
    )
    return proj


class TestDefaultAssembly:
    def test_codemode_and_the_mcp_anchor_are_built_in(self, mc, tmp_path):
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


class TestExposure:
    async def test_a_direct_tool_is_model_visible_and_script_callable(
        self, make_mc, tmp_path
    ):
        proj = _echo_project(tmp_path, "direct", exposure="direct")
        conversation = make_mc(config=_config({})).new_conversation(cwd=proj)
        wire(
            conversation,
            call_tool("codemode", {"script": SCRIPT_CALL_ECHO}, call_id="cm1"),
            say("done"),
        )
        await asyncio.wait_for(conversation.prepare(), BOUND)

        # the model is offered the tool, and the prompt announces the server
        assert "mcp__echo__echo" in conversation.tools.names(audience="model")
        prompt = conversation.agent.system_prompt
        assert "<mcp_servers>" in prompt
        assert "- echo: direct" in prompt

        answer = await asyncio.wait_for(
            conversation.chat("echo through the script tool"), BOUND
        )
        assert answer == "done"
        tool_messages = [m for m in conversation.messages if m["role"] == "tool"]
        assert len(tool_messages) == 1
        assert 'echo:{"x": "hi"}' in str(tool_messages[0]["content"])
        await conversation.aclose()

    async def test_auto_exposure_hides_tools_from_the_model_when_codemode_is_on(
        self, make_mc, tmp_path
    ):
        proj = _echo_project(tmp_path, "auto")  # no exposure key → auto
        conversation = make_mc(
            config=_config({"codemode": {"enabled": True}})
        ).new_conversation(cwd=proj)
        wire(
            conversation,
            call_tool("codemode", {"script": SCRIPT_CALL_ECHO}, call_id="cm1"),
            say("done"),
        )
        await asyncio.wait_for(conversation.prepare(), BOUND)
        # a codemode-exposure server connects in the background
        await _wait_for(
            lambda: "mcp__echo__echo" in conversation.tools.names(audience="program"),
            "the echo tool to register",
        )
        assert "mcp__echo__echo" not in conversation.tools.names(audience="model")

        answer = await asyncio.wait_for(
            conversation.chat("echo through the script tool"), BOUND
        )
        assert answer == "done"
        tool_messages = [m for m in conversation.messages if m["role"] == "tool"]
        assert 'echo:{"x": "hi"}' in str(tool_messages[0]["content"])
        await conversation.aclose()


class TestProgramOrigin:
    async def test_a_script_mcp_call_is_on_the_channel_but_never_a_message(
        self, make_mc, tmp_path
    ):
        proj = _echo_project(tmp_path, "origin", exposure="codemode")
        conversation = make_mc(
            config=_config({"codemode": {"enabled": True}})
        ).new_conversation(cwd=proj)
        wire(
            conversation,
            call_tool("codemode", {"script": SCRIPT_CALL_ECHO}, call_id="cm1"),
            say("done"),
        )
        await asyncio.wait_for(conversation.prepare(), BOUND)
        await _wait_for(
            lambda: conversation.tools.get("mcp__echo__echo") is not None,
            "the echo tool to register",
        )
        reader = conversation.subscribe()

        answer = await asyncio.wait_for(
            conversation.chat("echo through the script tool"), BOUND
        )
        assert answer == "done"
        seen = _drain(reader)

        # The script's MCP call was observable — as a program-origin event
        # nested under the codemode call.
        started = [
            e
            for e in seen
            if isinstance(e, ToolCallStarted) and e.name == "mcp__echo__echo"
        ]
        finished = [
            e
            for e in seen
            if isinstance(e, ToolCallFinished) and e.name == "mcp__echo__echo"
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
        assert conversation.agent.tool_call_count == 1

        # messages carry exactly one tool result — codemode's. The MCP call
        # never entered the conversation, and the nested id stays invisible.
        tool_messages = [m for m in conversation.messages if m["role"] == "tool"]
        assert len(tool_messages) == 1
        content = str(tool_messages[0]["content"])
        assert content.startswith("Script completed in ")
        assert 'echo:{"x": "hi"}' in content
        assert "cm1:1" not in content
        assert "mcp__echo__echo" not in content
        await conversation.aclose()


class TestTheCodemodeWarning:
    async def test_codemode_only_tools_warn_once_while_codemode_is_off(
        self, make_mc, tmp_path
    ):
        proj = _echo_project(tmp_path, "warn", exposure="codemode")
        conversation = make_mc(config=_config({})).new_conversation(cwd=proj)
        reader = conversation.subscribe()
        await asyncio.wait_for(conversation.prepare(), BOUND)

        warnings = []
        for _ in range(POLL_ATTEMPTS):
            warnings = [
                e
                for e in _drain(reader)
                if isinstance(e, Notice)
                and "reachable only through codemode" in e.message
            ]
            if warnings:
                break
            await asyncio.sleep(POLL)
        assert len(warnings) == 1
        assert warnings[0].level == "warn"
        # one conversation, one warning — a second emission never lands
        await asyncio.sleep(0.5)
        again = [
            e
            for e in _drain(reader)
            if isinstance(e, Notice) and "reachable only through codemode" in e.message
        ]
        assert again == []
        await conversation.aclose()


class TestClose:
    async def test_aclose_kills_the_server_and_shuts_the_runtime_down(
        self, make_mc, tmp_path
    ):
        proj = _echo_project(tmp_path, "close", exposure="codemode")
        conversation = make_mc(
            config=_config({"codemode": {"enabled": True}})
        ).new_conversation(cwd=proj)
        await asyncio.wait_for(conversation.prepare(), BOUND)
        runtime = conversation.tools.get("mcp_status").mcp_runtime
        await _wait_for(
            lambda: "mcp__echo__echo" in conversation.tools.names(audience="program"),
            "the echo tool to register",
        )
        session = runtime.sessions["echo"]
        proc = session._proc
        assert proc is not None and proc.returncode is None

        await asyncio.wait_for(conversation.aclose(), BOUND)

        for _ in range(POLL_ATTEMPTS):
            if proc.returncode is not None:
                break
            await asyncio.sleep(POLL)
        assert proc.returncode is not None  # the direct child is gone
        assert session.state == STATE_CLOSED

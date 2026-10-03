"""The mcp builtin plugin's child process — the real-subprocess contract.

Everything else in the suite drives the protocol through an in-process
wire peer (:mod:`tests.test_builtin_mcp`). What that cannot prove is the
child: that the configured command is spawned with the whole environment
plus the entry's overlay, that its stderr is kept as a tail, that response
bytes frame as newline-delimited JSON-RPC over the pipe, and that every
teardown path really reaps the direct child. Those facts live here, over
the same fake-server scripts the old suite used — Python scripts written
into ``tmp_path`` and launched with ``sys.executable``, no shell.

Every async path is bounded so a broken fake server can never hang the
suite on Windows, where only the direct child is killed. Waiting on the
child goes through ``tests.conftest.wait_until`` — the pidfile appears and
the pid disappears are conditions, not durations.
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest

from mocode.host.plugin.builtin.mcp.client import (
    ERA_LEGACY,
    ERA_MODERN,
    STATE_CLOSED,
    STATE_CONNECTED,
    McpError,
    McpSession,
)
from mocode.host.plugin.builtin.mcp.config import McpServerConfig

from ._mcp_fake import pidfile_env, write_mcp_json, write_server
from .conftest import wait_until

BOUND = 15  # seconds — every session operation in this file stays bounded

#: A modern-era fake with one tool that reports what the child sees, plus a
#: slow one for the timeout path — slow for a fraction of a second, so the
#: per-request budget is what fires and the teardown never waits it out.
MODERN_SERVER = r'''
import json, sys, os, time

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

pidfile = os.environ.get("MCP_TEST_PIDFILE")
if pidfile:
    open(pidfile, "w").write(str(os.getpid()))
sys.stderr.write("modern server starting\n")
sys.stderr.flush()

TOOLS = [
    {"name": "echo", "description": "Echo the arguments back",
     "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}},
                     "required": ["x"]}},
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), req.get("id")
    params = req.get("params") or {}
    if method == "server/discover":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28"],
            "capabilities": {"tools": {}},
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "echo-srv", "version": "1.0"}},
            "instructions": "Echo server instructions."}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": TOOLS, "ttlMs": 0, "cacheScope": "public"}})
    elif method == "tools/call":
        if "_meta" not in params:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "missing _meta"}})
        elif params.get("name") == "slow":
            time.sleep(0.3)
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "woke up"}]}})
        elif params.get("name") == "env":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text":
                json.dumps({"MARKER": os.environ.get("MARKER"),
                            "ENTRY_K": os.environ.get("ENTRY_K"),
                            "PLUGIN_ROOT": os.environ.get("PLUGIN_ROOT"),
                            "PATH_SET": "PATH" in os.environ})}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "resultType": "complete",
                "content": [{"type": "text", "text": "echo:" + json.dumps(params.get("arguments") or {})}],
                "structuredContent": {"args": params.get("arguments") or {}}}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''


def child_pidfile(tmp_path: Path, name: str) -> Path:
    """Where the *name* fake server writes its own pid."""
    return tmp_path / f"{name}.pid"


def server_config(
    script: Path,
    *,
    name: str = "demo",
    timeout: float = 30.0,
    pidfile: Path | None = None,
    **kwargs,
) -> McpServerConfig:
    """A stdio config for a fake *script*: its pidfile (so a test can watch
    the direct child) plus any environment overlay the test needs."""
    kwargs.setdefault("source", "test")
    env = dict(kwargs.pop("env", {}))
    if pidfile is not None:
        env.setdefault("MCP_TEST_PIDFILE", str(pidfile))
    return McpServerConfig(
        name=name,
        command=sys.executable,
        args=[str(script)],
        timeout=timeout,
        env=env,
        **kwargs,
    )


def child_alive(pid: int) -> bool:
    """Whether the direct child *pid* is still running.

    The SDK spawns the child inside a Job Object on Windows, which closes
    the ``PROCESS_ALL_ACCESS`` handle ``os.kill(pid, 0)`` asks for — so the
    probe there is a ``SYNCHRONIZE`` handle instead. POSIX: signal 0.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def wait_pidfile(pidfile: Path, *, bound: float = 5.0) -> int:
    """The fake server's own pid, once it has written it.

    The pidfile is a condition — the child has started — so this waits for
    it rather than guessing how long a spawn takes.
    """
    def written() -> bool:
        return pidfile.exists() and pidfile.read_text().strip().isdigit()

    assert await wait_until(written, bound=bound, what=f"{pidfile.name} to appear")
    return int(pidfile.read_text().strip())


async def wait_gone(pid: int, *, bound: float = 5.0) -> bool:
    """Wait (bounded) for a direct child to be reaped.

    The probe is synchronous — a handle lookup, not a wait — so the
    predicate stays a plain function the conftest poller can call.
    """
    return await wait_until(
        lambda: not child_alive(pid), bound=bound, what=f"pid {pid} to be reaped"
    )


class TestSpawnAndFraming:
    """The process contract: spawn, the wire over the pipe, the pid."""

    async def test_the_child_starts_and_speaks_the_modern_wire(self, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern.py", MODERN_SERVER),
            name="modern",
            pidfile=child_pidfile(tmp_path, "modern"),
        )
        session = McpSession(cfg)
        try:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
            assert session.era == ERA_MODERN
            assert session.protocol_version == "2026-07-28"
            assert session.server_info == {"name": "echo-srv", "version": "1.0"}
            assert session.instructions == "Echo server instructions."
            assert session.state == STATE_CONNECTED
            # the child really is running, and its pid is on file
            pid = await wait_pidfile(child_pidfile(tmp_path, "modern"))
            assert child_alive(pid)
            # newline-delimited JSON-RPC over the pipe: the call comes back
            # as the wire-form dict the tool mapping reads
            result = await asyncio.wait_for(session.call_tool("echo", {"x": "1"}), BOUND)
            assert result["content"] == [{"type": "text", "text": 'echo:{"x": "1"}'}]
            assert result["structuredContent"] == {"args": {"x": "1"}}
            assert result["isError"] is False
        finally:
            await asyncio.wait_for(session.close(), BOUND)

    async def test_stderr_is_collected_into_a_tail(self, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_err.py", MODERN_SERVER),
            name="err",
            pidfile=child_pidfile(tmp_path, "err"),
        )
        session = McpSession(cfg)
        try:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
            await asyncio.wait_for(session.list_tools(), BOUND)
            assert "modern server starting" in session.stderr_tail
        finally:
            await asyncio.wait_for(session.close(), BOUND)

    async def test_a_slow_child_times_the_call_out(self, tmp_path):
        """The per-request budget is enforced against a child that answers
        late — the call fails with a timeout rather than hanging."""
        cfg = server_config(
            write_server(tmp_path, "modern_slow.py", MODERN_SERVER),
            name="slow",
            timeout=0.2,
            pidfile=child_pidfile(tmp_path, "slow"),
        )
        session = McpSession(cfg)
        try:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
            with pytest.raises(McpError) as err:
                await asyncio.wait_for(session.call_tool("slow"), BOUND)
            assert err.value.code == "mcp_timeout"
            assert session.last_error and "timed out" in session.last_error
        finally:
            await asyncio.wait_for(session.close(), BOUND)

    async def test_the_environment_reaches_the_child(self, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_env.py", MODERN_SERVER),
            name="env",
            pidfile=child_pidfile(tmp_path, "env"),
            env={"MARKER": "from-parent", "ENTRY_K": "from-entry"},
        )
        session = McpSession(cfg)
        try:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
            result = await asyncio.wait_for(session.call_tool("env"), BOUND)
            import json

            seen = json.loads(result["content"][0]["text"])
            # the whole process environment plus the entry overlay — the SDK
            # layers its env over a trimmed platform default, so both must be
            # passed explicitly (decision D8)
            assert seen == {
                "MARKER": "from-parent",
                "ENTRY_K": "from-entry",
                "PLUGIN_ROOT": None,
                "PATH_SET": True,
            }
        finally:
            await asyncio.wait_for(session.close(), BOUND)


class TestTeardown:
    """Every teardown path reaps the direct child — close, sync shutdown, and
    a plugin close that kills what it built."""

    async def test_close_waits_for_the_child_to_die(self, tmp_path):
        pidfile = child_pidfile(tmp_path, "close")
        cfg = server_config(
            write_server(tmp_path, "modern_close.py", MODERN_SERVER),
            name="close",
            pidfile=pidfile,
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        pid = await wait_pidfile(pidfile)
        assert child_alive(pid)
        await asyncio.wait_for(session.close(), BOUND)
        assert await wait_gone(pid)
        assert session.state == STATE_CLOSED

    async def test_a_sync_shutdown_asks_the_child_to_die(self, tmp_path):
        pidfile = child_pidfile(tmp_path, "kill")
        cfg = server_config(
            write_server(tmp_path, "modern_kill.py", MODERN_SERVER),
            name="kill",
            pidfile=pidfile,
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        pid = await wait_pidfile(pidfile)
        session.shutdown()  # sync, non-blocking — the unwind is scheduled
        assert await wait_gone(pid)
        assert session.state == STATE_CLOSED

    async def test_an_unstartable_command_is_an_error(self, tmp_path):
        cfg = server_config(
            tmp_path / "missing.py",
            name="broken",
            pidfile=child_pidfile(tmp_path, "broken"),
        )
        cfg.command = "definitely-not-a-real-binary-mocode"
        session = McpSession(cfg)
        try:
            with pytest.raises(McpError) as err:
                await asyncio.wait_for(session.connect_and_register(), BOUND)
            assert "failed to start" in str(err.value)
            assert err.value.code in ("mcp_transport", "mcp_error")
        finally:
            session.shutdown()


class TestADroppedChild:
    async def test_a_killed_child_disconnects_the_session(self, tmp_path):
        """Killing the direct child is observed as a transport failure, not a
        hang — the request in flight fails with ``mcp_transport``."""
        pidfile = child_pidfile(tmp_path, "drop")
        cfg = server_config(
            write_server(tmp_path, "modern_drop.py", MODERN_SERVER),
            name="drop",
            pidfile=pidfile,
        )
        session = McpSession(cfg)
        try:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
            pid = await wait_pidfile(pidfile)
            # a request in flight when the child dies must fail, not hang
            task = asyncio.ensure_future(session.call_tool("slow"))
            if sys.platform == "win32":
                os.kill(pid, 9)
            else:
                os.kill(pid, 15)
            with pytest.raises(McpError) as err:
                await asyncio.wait_for(task, BOUND)
            assert err.value.code == "mcp_transport"
            assert "disconnected" in str(err.value)
        finally:
            await asyncio.wait_for(session.close(), BOUND)


class TestTheMocodeFakeSeam:
    """The shared fake helpers are themselves the seam the cross-plugin tests
    use — they are exercised here so their contract is not assumed."""

    def test_the_fake_answers_with_its_pid_on_request(self, tmp_path):
        script = write_server(tmp_path, "fake_echo.py", MODERN_SERVER)
        assert script.exists()
        assert "server/discover" in script.read_text(encoding="utf-8")

    def test_the_mcp_json_is_a_valid_table(self, tmp_path):
        path = write_mcp_json(tmp_path / ".mocode" / "mcp.json", {"echo": {"type": "stdio", "command": "python"}})
        import json

        table = json.loads(path.read_text(encoding="utf-8"))
        assert list(table["mcpServers"]) == ["echo"]
        assert table["mcpServers"]["echo"]["type"] == "stdio"

    def test_the_pidfile_env_names_a_watchable_child(self, tmp_path):
        env = pidfile_env(tmp_path, "echo")
        assert env["MCP_TEST_PIDFILE"].endswith("echo.pid")

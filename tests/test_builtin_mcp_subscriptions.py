"""The modern tool-change subscription — ``subscriptions/listen``.

Two seams, one per job. The event path and the registry reconciliation are
exercised through an **in-process ``mcp.server.MCPServer`` with an explicit
``InMemorySubscriptionBus``** — a test publishes ``ToolsListChanged`` the way
a server would — connected to through ``McpSession(server=...)`` under a real
``McpRuntime``: the assertions are on what the registry ends up holding, not
on any internal of the session. Because the server cannot deliver an event
published before the listen stream's acknowledgment, the in-process tests
re-publish (one identical event coalesces, so re-publishing is idempotent)
until the reconciliation lands — which is also what the spec prescribes for
a client that may have missed a change: re-listen and re-fetch.

The retry driver (``SubscriptionLost`` → backoff → re-listen;
``ListenNotSupportedError`` / ``MCPError`` → report and end) is driven
against a scripted stub of the SDK's listen stream, so a drop is provoked
without a network. A stdio fake carries the legacy-era case: no subscription,
connection intact. Every async path is bounded by ``BOUND`` so a broken fake
can never hang the suite on Windows.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest
import pytest_asyncio
from mcp.client.subscriptions import ListenNotSupportedError, SubscriptionLost
from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ToolsListChanged

from mocode.host.config import Config
from mocode.host.plugin.builtin.mcp.client import (
    ERA_LEGACY,
    ERA_MODERN,
    STATE_CONNECTED,
    McpSession,
)
from mocode.host.plugin.builtin.mcp.config import McpServerConfig
from mocode.host.plugin.builtin.mcp.runtime import McpRuntime
from mocode.host.plugin.builtin.mcp.subscriptions import (
    BACKOFF_INITIAL,
    watch_tools,
)
from mocode.host.plugin.context import BuildContext

BOUND = 15  # seconds — every async path in this file stays bounded

# ── helpers ───────────────────────────────────────────────


def _greet(name: str) -> str:
    return f"hi {name}"


def _late() -> str:
    return "late"


def _plain() -> str:
    return "plain"


async def _noop() -> None:
    """A change callback that answers by doing nothing."""


async def wait_until(predicate, *, timeout: float = BOUND) -> bool:
    """Poll *predicate* until it holds — every assertion on an event that
    travels through the subscription goes through this, bounded."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


class InProc:
    """An in-process modern server plus the bus that publishes its changes."""

    def __init__(self) -> None:
        self.bus = InMemorySubscriptionBus()
        self.server = MCPServer(
            name="inproc", version="9.9.9", instructions="In-proc.", subscriptions=self.bus
        )
        self.server.add_tool(_greet, name="greet", description="Say hi")
        self.server.add_tool(_plain, name="plain")

    def add(self, name: str) -> None:
        self.server.add_tool(_late, name=name, description="Arrived later")

    def remove(self, name: str) -> None:
        self.server.remove_tool(name)

    async def announce(self, times: int = 1) -> None:
        """Publish the tool-list-changed cue; a change published before the
        stream acknowledged is lost, so the caller re-publishes (identical
        events coalesce while unconsumed) until the reconciliation lands."""
        for _ in range(times):
            await self.bus.publish(ToolsListChanged())


def inproc_config(name: str = "demo") -> McpServerConfig:
    """The config shape an in-process session needs — the ``server=`` seam
    overrides the target, so only the bookkeeping matters."""
    return McpServerConfig(name=name, source="test")


def make_runtime(tmp_path: Path, servers: dict) -> McpRuntime:
    """A real runtime over ``servers`` — the in-process session is attached
    to its callbacks and its key by the caller."""
    config = Config(provider="p", model="m", plugins={"mcp": {"servers": servers}})
    return McpRuntime(
        BuildContext(home=tmp_path / "home", cwd=tmp_path, config=config)
    )


@pytest_asyncio.fixture
async def runtime_factory(tmp_path):
    """Build runtimes over in-process servers; close their sessions on
    teardown. The session is wired exactly as the runtime's own connect
    wires it — the same two callbacks, under the folded key."""
    sessions: list[McpSession] = []

    def factory() -> tuple[McpRuntime, InProc]:
        env = InProc()
        runtime = make_runtime(tmp_path, {"demo": {"command": "never-run"}})
        session = McpSession(
            inproc_config(),
            server=env.server,
            on_connected=runtime._on_connected,
            on_tools_changed=runtime._on_tools_changed,
        )
        runtime.sessions["demo"] = session
        sessions.append(session)
        return runtime, env

    yield factory
    for session in sessions:
        try:
            await asyncio.wait_for(session.close(), BOUND)
        except Exception:
            session.shutdown()


async def wait_for_change(env: InProc, predicate, *, timeout: float = BOUND) -> bool:
    """Announce the change until the subscription delivers it — one
    identical event coalesces while unconsumed, so this is idempotent."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await env.announce()
        await asyncio.sleep(0.05)
    return predicate()


# ── an in-process modern server — the event path ───────────


class TestModernToolSubscription:
    async def test_a_change_registers_the_new_tool_and_unregisters_the_gone(
        self, runtime_factory
    ):
        runtime, env = runtime_factory()
        session = runtime.sessions["demo"]
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        registry = runtime._ctx.tools
        assert "mcp__demo__greet" in registry

        env.add("late")
        assert await wait_for_change(env, lambda: "mcp__demo__late" in registry)

        env.remove("late")
        env.remove("greet")
        assert await wait_for_change(
            env,
            lambda: "mcp__demo__late" not in registry
            and "mcp__demo__greet" not in registry,
        )
        # the untouched tool stays exactly where the connect put it
        assert "mcp__demo__plain" in registry

    async def test_the_subscription_lives_and_dies_with_the_connection(
        self, runtime_factory
    ):
        runtime, env = runtime_factory()
        session = runtime.sessions["demo"]
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        # started by the connection: a private ref the session cancels on the
        # way out, and a change delivered through it while it runs
        assert session._watch_task is not None
        env.add("late")
        registry = runtime._ctx.tools
        assert await wait_for_change(env, lambda: "mcp__demo__late" in registry)

        await asyncio.wait_for(session.close(), BOUND)
        assert session._watch_task is None

    async def test_a_sync_shutdown_ends_the_subscription_too(self, runtime_factory):
        runtime, env = runtime_factory()
        session = runtime.sessions["demo"]
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session._watch_task is not None
        session.shutdown()
        assert session._watch_task is None
        assert session.state == "closed"

    async def test_a_modern_session_without_a_callback_starts_no_subscription(
        self,
    ):
        env = InProc()
        session = McpSession(inproc_config(), server=env.server)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session._watch_task is None  # nobody to answer a change
        await asyncio.wait_for(session.close(), BOUND)


# ── a stdio legacy server — no subscription, connection intact ──


LEGACY_SERVER = r'''
import json, sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

TOOLS = [
    {"name": "echo", "description": "Echo arguments",
     "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}},
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
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "legacy-srv", "version": "1.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        if params.get("name") == "bump":
            TOOLS.append({"name": "late", "description": "Arrived later",
                          "inputSchema": {"type": "object", "properties": {}}})
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "bumped"}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "echo"}]}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''


def legacy_config(tmp_path: Path, name: str = "legacy") -> McpServerConfig:
    """A stdio config for the legacy fake, spawned with the test's own
    interpreter — no shell."""
    script = tmp_path / "legacy_server.py"
    script.write_text(textwrap.dedent(LEGACY_SERVER), encoding="utf-8")
    return McpServerConfig(
        name=name,
        source="test",
        command=sys.executable,
        args=[str(script)],
        timeout=30.0,
    )


class TestLegacyConnection:
    async def test_a_legacy_connection_never_starts_a_subscription(self, tmp_path):
        changes: list[list[str]] = []

        async def on_tools_changed(session):
            # what the runtime's sync_tools does with the notification
            changes.append([t["name"] for t in await session.list_tools()])

        session = McpSession(
            legacy_config(tmp_path), on_tools_changed=on_tools_changed
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.state == STATE_CONNECTED
        assert session._watch_task is None  # pre-2026: the notification path, only

        # the connection itself is intact: a call works, and the legacy
        # notification — not a subscription — drives exactly one re-sync
        result = await asyncio.wait_for(session.call_tool("bump"), BOUND)
        assert result["isError"] is False
        assert await wait_until(lambda: len(changes) == 1)
        assert changes[0] == ["echo", "late"]

        await asyncio.wait_for(session.close(), BOUND)
        assert session._watch_task is None


# ── the retry driver — a scripted stub of the SDK stream ────


class _ScriptedStream:
    """One stubbed ``subscriptions/listen`` stream: it yields *events* and
    then ends — ``"lost"`` as an abrupt drop, ``"closed"`` as the server's
    graceful close."""

    def __init__(self, events: list, end: str) -> None:
        self._events = list(events)
        self._end = end

    async def __aenter__(self) -> "_ScriptedStream":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def __aiter__(self) -> "_ScriptedStream":
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        if self._end == "lost":
            raise SubscriptionLost("the stub's stream dropped")
        raise StopAsyncIteration


class _ScriptedClient:
    """The slice of the SDK client ``watch_tools`` needs: ``listen()`` opens
    the next scripted stream — one script entry per attempt, the last
    repeating — and records when and with which filter, so a test reads the
    backoff off it."""

    def __init__(self, scripts: list[tuple[list, str]]) -> None:
        self._scripts = scripts
        self.attempts = 0
        self.filters: list[dict] = []
        self.opened_at: list[float] = []

    def listen(self, **filters):
        self.attempts += 1
        self.filters.append(dict(filters))
        self.opened_at.append(asyncio.get_running_loop().time())
        index = min(self.attempts - 1, len(self._scripts) - 1)
        events, end = self._scripts[index]
        return _ScriptedStream(events, end)

    @property
    def gaps(self) -> list[float]:
        """Seconds between successive listen attempts."""
        return [b - a for a, b in zip(self.opened_at, self.opened_at[1:])]


class _RefusingClient:
    """An SDK client whose ``listen()`` refuses with *error* outright."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def listen(self, **filters):
        self.calls += 1
        raise self.error


async def drive(client, on_changed, *, report=None) -> asyncio.Task:
    """``watch_tools`` in its own task — the way the session runs it — ready
    to cancel."""
    task = asyncio.create_task(watch_tools(client, on_changed, report=report))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return task


class TestWatchTools:
    async def test_a_stream_event_drives_the_change_callback(self):
        client = _ScriptedClient([([ToolsListChanged()], "closed")])
        seen: list[int] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed)
        assert await wait_until(lambda: seen)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # the filter is tools-only: nothing else on the modern vocabularies
        assert client.filters and client.filters[0] == {"tools_list_changed": True}

    async def test_a_dropped_stream_re_listens_after_the_backoff(self):
        client = _ScriptedClient([([], "lost"), ([ToolsListChanged()], "closed")])
        seen: list[int] = []
        reports: list[str] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed, report=reports.append)
        assert await wait_until(lambda: len(seen) >= 2), client.opened_at
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert client.attempts >= 3  # the drop cost at least one re-listen
        assert client.gaps[0] >= BACKOFF_INITIAL - 0.05  # and it waited first
        assert any("dropped" in message for message in reports)

    async def test_the_backoff_doubles_and_an_event_resets_it(self):
        client = _ScriptedClient(
            [
                ([], "lost"),
                ([ToolsListChanged()], "lost"),
                ([], "lost"),
            ]
        )
        seen: list[int] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed, report=lambda _message: None)
        assert await wait_until(lambda: client.attempts >= 4), client.opened_at
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        gaps = client.gaps
        assert gaps[0] >= BACKOFF_INITIAL - 0.05
        assert gaps[1] >= BACKOFF_INITIAL - 0.05  # the event reset the doubling
        assert gaps[2] >= 2 * BACKOFF_INITIAL - 0.05  # before the ceiling

    async def test_a_graceful_close_re_listens(self):
        client = _ScriptedClient([([], "closed"), ([ToolsListChanged()], "closed")])
        seen: list[int] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed)
        assert await wait_until(lambda: client.attempts >= 2 and seen), client.opened_at
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert client.gaps[0] >= BACKOFF_INITIAL - 0.05

    async def test_a_refused_subscription_is_raised_never_retried(self):
        client = _RefusingClient(ListenNotSupportedError("2025-11-25"))
        with pytest.raises(ListenNotSupportedError):
            await asyncio.wait_for(watch_tools(client, _noop), BOUND)
        assert client.calls == 1

    async def test_a_failed_subscription_is_reported_and_raised(self):
        client = _RefusingClient(MCPError(-32601, "the server refuses subscriptions"))
        reports: list[str] = []
        with pytest.raises(MCPError):
            await asyncio.wait_for(
                watch_tools(client, _noop, report=reports.append), BOUND
            )
        assert client.calls == 1
        assert reports  # said before it was raised

    async def test_a_failing_re_sync_is_reported_and_the_stream_continues(self):
        client = _ScriptedClient(
            [([ToolsListChanged(), ToolsListChanged()], "closed")]
        )
        reports: list[str] = []
        calls: list[int] = []

        async def on_changed():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("the registry hiccuped")

        task = await drive(client, on_changed, report=reports.append)
        assert await wait_until(lambda: len(calls) >= 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert len(reports) == 1 and "registry hiccuped" in reports[0]

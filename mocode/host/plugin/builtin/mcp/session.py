"""One stdio MCP connection — spawn, era probe, requests, teardown.

MCP stdio is newline-delimited JSON-RPC 2.0: the model's side of the
conversation never sees it — this module is the whole wire. The client is
**dual-era** (decision D13): current servers answer ``server/discover``
(modern, 2026-07-28 — no handshake, every request carries a ``_meta`` with
protocol version, client info and capabilities, results may carry a
``resultType``), while the many still-legacy servers (≤2025-11-25) need the
``initialize`` handshake and ``notifications/initialized``. The era is
probed once per server and cached on the session; a dropped connection
reconnects on the next call with the era cache intact. The standard is
explicit that anything but a proper ``DiscoverResult`` (or a recognized
modern negotiation error) identifies a legacy server — a single error code
never decides the era.

The child is spawned without a shell, in its own process group on POSIX so
teardown takes the whole tree; Windows has no process groups, so only the
direct child is killed (a known limitation, as in the shell session).
stderr is server logging — collected into a bounded tail for error reports,
never mistaken for a protocol error.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections import deque
from typing import Any, Awaitable, Callable

from ...loader import report
from .config import McpServerConfig
from .rpc import decode_line, encode_message

#: Modern protocol era (current specification).
ERA_MODERN = "modern"
#: Legacy protocol era (≤2025-11-25, initialize handshake).
ERA_LEGACY = "legacy"

STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"
STATE_ERROR = "error"
STATE_CLOSED = "closed"

_MODERN_VERSION = "2026-07-28"
_LEGACY_VERSION = "2025-11-25"
_CLIENT_INFO = {"name": "mocode", "version": "0.4.0"}
#: Recognized modern negotiation errors (HeaderMismatch,
#: MissingRequiredClientCapability, UnsupportedProtocolVersion).
_MODERN_ERROR_CODES = frozenset({-32020, -32021, -32022})

#: How long the era probe waits for server/discover before treating the
#: server as legacy.
_DISCOVER_TIMEOUT = 5.0
#: Lines of server logging kept for error reports.
_STDERR_TAIL_LINES = 50
#: The per-request default when neither the call nor the config sets one.
_DEFAULT_REQUEST_TIMEOUT = 60.0
#: close(): stdin close → SIGTERM grace → SIGKILL grace.
_STDIN_CLOSE_GRACE = 1.0
_TERM_GRACE = 2.0
_KILL_GRACE = 2.0

#: POSIX's "kill it now" — absent on Windows, where the line never runs.
_SIGKILL = getattr(signal, "SIGKILL", 9)


class McpError(Exception):
    """An MCP transport or protocol error.

    ``code`` is the mocode-side category (``mcp_transport``,
    ``mcp_timeout``, ``mcp_input_required``); ``rpc_code`` / ``data`` carry
    the JSON-RPC error details when there were any (the era probe reads
    them).
    """

    def __init__(
        self,
        message: str,
        code: str = "mcp_transport",
        *,
        rpc_code: int | None = None,
        data: Any = None,
    ):
        self.code = code
        self.rpc_code = rpc_code
        self.data = data
        super().__init__(message)


def _meta_for(version: str) -> dict:
    """The modern per-request ``_meta`` block (frozen shape, ref §6)."""
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": dict(_CLIENT_INFO),
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _terminate(proc: "asyncio.subprocess.Process", *, sig: int) -> None:
    """Signal the child — its whole group on POSIX, the direct child on
    Windows (no process groups there)."""
    if proc.returncode is not None:
        return
    if sys.platform != "win32":
        try:
            os.killpg(proc.pid, sig)
            return
        except (ProcessLookupError, PermissionError):
            pass  # the group is gone — fall through to the direct child
    if sig == signal.SIGTERM:
        proc.terminate()
    else:
        proc.kill()


class StdioSession:
    """One stdio connection to one configured MCP server.

    ``connect()`` spawns the child and resolves the era (cached across
    reconnects); :meth:`list_tools` and :meth:`call_tool` speak whichever
    era the server is; :meth:`close` escalates stdin-close → terminate →
    kill. ``on_connected``/``on_tools_changed`` are how the runtime learns
    about this session's tool list — this class never touches the registry.
    """

    def __init__(
        self,
        config: McpServerConfig,
        *,
        on_connected: (
            Callable[["StdioSession", list[dict]], Awaitable[None]] | None
        ) = None,
        on_tools_changed: Callable[["StdioSession"], Awaitable[None]] | None = None,
        discover_timeout: float = _DISCOVER_TIMEOUT,
    ):
        self.config = config
        self.name = config.name
        self.on_connected = on_connected
        self.on_tools_changed = on_tools_changed
        self._discover_timeout = discover_timeout

        self.era: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict = {}
        self.instructions: str = ""
        self.state: str = STATE_DISCONNECTED
        self.last_error: str | None = None
        #: The most recent tools/list result — what the runtime registered.
        self.tools: list[dict] = []

        self._proc: "asyncio.subprocess.Process | None" = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._notify_tasks: set[asyncio.Task] = set()
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._send_lock = asyncio.Lock()

    # ── introspection ───────────────────────────────────────

    @property
    def stderr_tail(self) -> list[str]:
        """The last lines the server wrote to stderr — report material."""
        return list(self._stderr_tail)

    # ── lifecycle ───────────────────────────────────────────

    async def connect(self) -> None:
        """Spawn the child (again, after a drop) and settle the era.

        The era probe runs once per session; a reconnect reuses the cached
        era but re-runs whatever greeting that era needs on the fresh
        process. A closed session stays closed.
        """
        if self.state == STATE_CLOSED:
            raise McpError("session is closed", "mcp_closed")
        if (
            self.state == STATE_CONNECTED
            and self._proc is not None
            and self._proc.returncode is None
        ):
            return
        self.state = STATE_CONNECTING
        try:
            await self._ensure_spawned()
            if self.era is None:
                await self._probe_era()
            if self.era == ERA_LEGACY:
                await self._legacy_initialize()
        except asyncio.CancelledError:
            await self._kill_process()
            raise
        except Exception as e:
            self.state = STATE_ERROR
            self.last_error = str(e)
            await self._kill_process()
            raise
        self.state = STATE_CONNECTED
        self.last_error = None

    async def ensure_connected(self) -> None:
        """Reconnect once after a drop; raise when the session is closed."""
        if (
            self.state == STATE_CONNECTED
            and self._proc is not None
            and self._proc.returncode is None
        ):
            return
        if self.state == STATE_CLOSED:
            raise McpError("session is closed", "mcp_closed")
        await self.connect()

    async def connect_and_register(self) -> None:
        """The bounded-wait entry for direct servers: connect, list tools,
        hand them to the runtime through ``on_connected``."""
        await self.connect()
        tools = await self.list_tools()
        if self.on_connected is not None:
            await self.on_connected(self, tools)

    async def close(self) -> None:
        """Graceful teardown: close stdin, wait, terminate, wait, kill.

        The escalating ladder gives a well-behaved server three chances to
        go on its own; whatever is left after the last grace is reaped.
        """
        if self.state == STATE_CLOSED:
            return
        self.state = STATE_CLOSED
        for fut in list(self._pending.values()):
            fut.cancel()
        self._pending.clear()
        for task in self._notify_tasks:
            task.cancel()
        self._notify_tasks.clear()
        proc = self._proc
        if proc is None:
            await self._stop_pumps()
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), _STDIN_CLOSE_GRACE)
        except asyncio.TimeoutError:
            pass
        if proc.returncode is None:
            _terminate(proc, sig=signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), _TERM_GRACE)
            except asyncio.TimeoutError:
                pass
        if proc.returncode is None:
            _terminate(proc, sig=_SIGKILL)
            try:
                await asyncio.wait_for(proc.wait(), _KILL_GRACE)
            except asyncio.TimeoutError:
                pass
        await self._stop_pumps()

    def shutdown(self) -> None:
        """Sync teardown for the plugin's ``close()`` — hard kill, no I/O.

        Mirrors the shell session's shutdown: the conversation is ending,
        so there is no grace period to give; POSIX takes the whole process
        group, Windows the direct child. The pump tasks are cancelled, not
        awaited — the loop reaps them.
        """
        self.state = STATE_CLOSED
        for fut in list(self._pending.values()):
            fut.cancel()
        self._pending.clear()
        for task in (self._reader_task, self._stderr_task, *self._notify_tasks):
            if task is not None and not task.done():
                task.cancel()
        self._notify_tasks.clear()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass
            _terminate(proc, sig=_SIGKILL)

    # ── MCP methods ─────────────────────────────────────────

    async def list_tools(self) -> list[dict]:
        """All tools the server offers, paging through ``nextCursor``."""
        await self.ensure_connected()
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params)
            page = result.get("tools")
            if isinstance(page, list):
                tools.extend(t for t in page if isinstance(t, dict))
            nxt = result.get("nextCursor")
            cursor = nxt if isinstance(nxt, str) and nxt else None
            if not cursor:
                break
        self.tools = tools
        return tools

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call one tool by its raw MCP name; the raw result dict comes back.

        A modern ``resultType: "input_required"`` (MRTR) raises — mocode v1
        has no elicitation UI. ``isError`` results come back untouched; the
        tool wrapper decides what they mean.
        """
        await self.ensure_connected()
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if result.get("resultType") == "input_required":
            raise McpError(
                "server requires user input; elicitation is not supported",
                "mcp_input_required",
            )
        return result

    # ── wire plumbing ───────────────────────────────────────

    def _meta(self) -> dict:
        return _meta_for(self.protocol_version or _MODERN_VERSION)

    async def _ensure_spawned(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        env = dict(os.environ)
        env.update(self.config.env)
        if self.config.plugin_root is not None:
            env["PLUGIN_ROOT"] = str(self.config.plugin_root)
        if self.config.plugin_data is not None:
            env["PLUGIN_DATA"] = str(self.config.plugin_data)
        kwargs = {} if sys.platform == "win32" else {"start_new_session": True}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.config.cwd,
                env=env,
                **kwargs,
            )
        except Exception as e:
            raise McpError(f"failed to start {self.config.command!r}: {e}")
        self._reader_task = asyncio.create_task(self._read_loop(self._proc))
        self._stderr_task = asyncio.create_task(self._stderr_loop(self._proc))

    async def _request(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
        modern: bool | None = None,
    ) -> dict:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("server is not connected")
        if modern is None:
            modern = self.era == ERA_MODERN
        self._next_id += 1
        rid = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        message: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        if modern:
            # The era probe passes its own negotiated _meta — never clobber
            # an explicit one.
            message.setdefault("params", {}).setdefault("_meta", self._meta())
        try:
            async with self._send_lock:
                self._proc.stdin.write(encode_message(message))
                await self._proc.stdin.drain()
        except Exception as e:
            self._pending.pop(rid, None)
            raise McpError(f"failed to send {method}: {e}")
        if timeout is not None:
            deadline = timeout
        elif self.config.timeout is not None:
            deadline = self.config.timeout
        else:
            deadline = _DEFAULT_REQUEST_TIMEOUT
        try:
            return await asyncio.wait_for(fut, deadline)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            self.last_error = f"{method} timed out after {deadline}s"
            raise McpError(self.last_error, "mcp_timeout")

    async def _send_notification(self, method: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("server is not connected")
        async with self._send_lock:
            self._proc.stdin.write(
                encode_message({"jsonrpc": "2.0", "method": method})
            )
            await self._proc.stdin.drain()

    async def _read_loop(self, proc: "asyncio.subprocess.Process") -> None:
        """Route each incoming line: responses to their futures, the legacy
        list-changed notification to the runtime, everything else ignored."""
        stdout = proc.stdout
        if stdout is None:  # pragma: no cover - defensive
            return
        while True:
            try:
                line = await stdout.readline()
            except Exception:
                break
            if not line:
                break
            if not line.strip():
                continue
            message = decode_line(line)
            if message is None:
                report(
                    f"mcp: {self.name}: dropping an unparseable line from the server"
                )
                continue
            self._dispatch_incoming(message)
        self._on_disconnect()

    def _dispatch_incoming(self, message: dict) -> None:
        rid = message.get("id")
        if rid is not None:
            fut = self._pending.pop(rid, None)
            if fut is None or fut.done():
                return  # a late answer to a timed-out request
            error = message.get("error")
            if error is not None:
                if isinstance(error, dict):
                    fut.set_exception(
                        McpError(
                            f"{error.get('message', 'unknown error')} "
                            f"(code {error.get('code', 0)})",
                            rpc_code=error.get("code")
                            if isinstance(error.get("code"), int)
                            else None,
                            data=error.get("data"),
                        )
                    )
                else:
                    fut.set_exception(McpError(str(error)))
            else:
                fut.set_result(message.get("result") or {})
            return
        if (
            message.get("method") == "notifications/tools/list_changed"
            and self.on_tools_changed is not None
        ):
            task = asyncio.create_task(self.on_tools_changed(self))
            self._notify_tasks.add(task)
            task.add_done_callback(self._notify_tasks.discard)

    def _on_disconnect(self) -> None:
        # A failed connect owns its ERROR state, and close() owns CLOSED —
        # the read loop exiting (we killed the child) must not clobber either.
        if self.state in (STATE_CLOSED, STATE_ERROR):
            return
        self.state = STATE_DISCONNECTED
        self.last_error = "server disconnected"
        error = McpError("server disconnected")
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(error)
        self._pending.clear()

    async def _stderr_loop(self, proc: "asyncio.subprocess.Process") -> None:
        """Consume stderr into the bounded tail — logging, not errors."""
        stderr = proc.stderr
        if stderr is None:  # pragma: no cover - defensive
            return
        while True:
            try:
                line = await stderr.readline()
            except Exception:
                return
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                self._stderr_tail.append(text)

    async def _kill_process(self) -> None:
        """Best-effort kill after a failed connect — frees the pipes."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            _terminate(proc, sig=_SIGKILL)
            try:
                await asyncio.wait_for(proc.wait(), _KILL_GRACE)
            except asyncio.TimeoutError:
                pass
        await self._stop_pumps()
        self._proc = None

    async def _stop_pumps(self) -> None:
        pumps = [t for t in (self._reader_task, self._stderr_task) if t is not None]
        for task in pumps:
            task.cancel()
        if pumps:
            await asyncio.gather(*pumps, return_exceptions=True)
        self._reader_task = None
        self._stderr_task = None

    # ── era resolution ──────────────────────────────────────

    async def _probe_era(self) -> None:
        """Probe with server/discover once; cache the era on the session.

        A proper ``DiscoverResult`` naming ``2026-07-28`` is modern; a
        recognized modern negotiation error (``-32020..-32022``) is modern
        too, and asks for a retry at the newest supported version at or past
        ``2026-07-28`` — with none, the server falls back to legacy.
        **Anything else — another error, a timeout, a wrong shape — means
        legacy**; the standard forbids deciding on a single error code.
        """
        try:
            result = await self._request(
                "server/discover",
                {"_meta": _meta_for(_MODERN_VERSION)},
                timeout=self._discover_timeout,
                modern=True,
            )
        except McpError as e:
            if e.rpc_code in _MODERN_ERROR_CODES:
                supported = e.data.get("supported") if isinstance(e.data, dict) else None
                candidates = sorted(
                    v
                    for v in (supported if isinstance(supported, list) else [])
                    if isinstance(v, str) and v >= _MODERN_VERSION
                )
                if candidates:
                    version = candidates[-1]
                    try:
                        result = await self._request(
                            "server/discover",
                            {"_meta": _meta_for(version)},
                            timeout=self._discover_timeout,
                            modern=True,
                        )
                    except McpError:
                        self._become_legacy()
                        return
                    versions = result.get("supportedVersions")
                    if isinstance(versions, list) and version in versions:
                        self._become_modern(version, result)
                    else:
                        self._become_legacy()
                    return
            self._become_legacy()
            return
        versions = result.get("supportedVersions")
        if isinstance(versions, list) and _MODERN_VERSION in versions:
            self._become_modern(_MODERN_VERSION, result)
        else:
            self._become_legacy()

    def _become_modern(self, version: str, discover_result: dict) -> None:
        self.era = ERA_MODERN
        self.protocol_version = version
        meta = discover_result.get("_meta")
        if isinstance(meta, dict):
            info = meta.get("io.modelcontextprotocol/serverInfo")
            if isinstance(info, dict):
                self.server_info = info
        instructions = discover_result.get("instructions")
        if isinstance(instructions, str):
            self.instructions = instructions

    def _become_legacy(self) -> None:
        self.era = ERA_LEGACY

    async def _legacy_initialize(self) -> None:
        """The ≤2025-11-25 handshake: initialize → result → initialized."""
        result = await self._request(
            "initialize",
            {
                "protocolVersion": _LEGACY_VERSION,
                "capabilities": {},
                "clientInfo": dict(_CLIENT_INFO),
            },
            modern=False,
        )
        version = result.get("protocolVersion")
        self.protocol_version = version if isinstance(version, str) else _LEGACY_VERSION
        info = result.get("serverInfo")
        if isinstance(info, dict):
            self.server_info = info
        instructions = result.get("instructions")
        if isinstance(instructions, str):
            self.instructions = instructions
        await self._send_notification("notifications/initialized")

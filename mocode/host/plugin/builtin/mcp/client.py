"""One MCP connection over the official SDK — spawn, negotiate, requests, teardown.

The SDK (v2) carries the wire: ``mode="auto"`` probes ``server/discover`` and
falls back to the ``initialize`` handshake (dual-era, decision D13), the stdio
transport spawns the child in its own kill scope and shuts it down the way
the standard prescribes, the HTTP transports ride a pre-configured client
(the entry's fixed headers, the session's timeouts), and every result is
typed. What is left here is the seam towards mocode:

* the session surface the runtime and the tool builders call
  (:meth:`McpSession.connect_and_register`, :meth:`~McpSession.list_tools`,
  :meth:`~McpSession.call_tool`, :meth:`~McpSession.close`,
  :meth:`~McpSession.shutdown`) — unchanged from the hand-written client;
* the wire-form dicts those calls answer with (``inputSchema`` /
  ``structuredContent`` / ``mimeType``), so the tool mapping and its error
  codes stay as they were;
* the mocode-side error categories (``mcp_transport``, ``mcp_timeout``,
  ``mcp_input_required``, ``mcp_closed``, ``mcp_error``);
* the child's environment, passed in full because the SDK layers its
  ``env`` over a trimmed platform default (decision D8).

A session never touches the registry: the runtime learns about a tool set
through the ``on_connected`` / ``on_tools_changed`` callbacks.

The client is an async context manager, and **one that can only be left in
the task that entered it**: exiting the SDK's session cancels its anyio task
group's scope, whose task set still contains the entering task — so an
unwind from any other task cancels the caller that connected. The session
therefore holds the client inside its own connection task
(:meth:`~McpSession._serve`), which enters it, serves the session's calls
from whoever makes them, and leaves it — killing the child on the way out —
when the session is closed or the connection is replaced by a reconnect. The
same rule is why a dropped connection is not retried on the call that
noticed it: the next call starts a fresh connection task, because a client
cannot be re-entered.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import McpServerConfig

if TYPE_CHECKING:
    from mcp import Client as SdkClient
    from mcp.types import ServerCapabilities

#: Modern protocol era (current specification).
ERA_MODERN = "modern"
#: Legacy protocol era (≤2025-11-25, initialize handshake).
ERA_LEGACY = "legacy"

STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"
STATE_ERROR = "error"
STATE_CLOSED = "closed"

#: The modern protocol version the era name derives from.
_MODERN_VERSION = "2026-07-28"
#: How the client identifies itself to the server.
_CLIENT_INFO = {"name": "mocode", "version": "0.4.0"}
#: The per-request default when neither the call nor the config sets one.
_DEFAULT_REQUEST_TIMEOUT = 60.0
#: How long an HTTP transport may take to open a connection — the runtime
#: bounds the whole connect at its own ``connect_timeout_s`` (default 10).
_HTTP_CONNECT_TIMEOUT = 10.0
#: How long an SSE stream may stay idle before it counts as dropped — the
#: SDK's own read-timeout default for the legacy transport.
_SSE_READ_TIMEOUT = 300.0
#: Lines of server logging kept for error reports.
_STDERR_TAIL_LINES = 50
#: How much of the stderr log to read back when producing the tail.
_STDERR_TAIL_BYTES = 65536
#: Bound on waiting for a connection task to unwind (the SDK's own shutdown
#: is bounded too, but a stuck child must not hold the caller).
_EXIT_BOUND = 15.0


def _unwrap(error: BaseException) -> BaseException:
    """The real failure inside an anyio task-group wrap.

    The SDK connects inside a task group, and a failure there propagates out
    of ``__aenter__`` as an ``ExceptionGroup`` (anyio collects the
    propagating exception) — the one sub-exception is the error to map and
    report. Anything but a single-sub-exception group is left alone.
    """
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


class McpError(Exception):
    """An MCP transport, timeout or protocol failure on the mocode side.

    ``code`` is the mocode-side category — ``mcp_transport``,
    ``mcp_timeout``, ``mcp_input_required`` or ``mcp_closed``; the tool
    builders map it onto a :class:`~mocode.core.tool.ToolError` and the set
    of those codes is a hard contract.
    """

    def __init__(self, message: str, code: str = "mcp_transport"):
        self.code = code
        super().__init__(message)


class _StderrLog:
    """The child's stderr, kept as a tail for status and error reports.

    The SDK hands ``errlog`` to the child at spawn, which means an object
    with only ``write``/``flush`` never sees a line — the transport routes
    the child to the inherited OS handle, not through Python. A temporary
    file is what actually captures the tail on every platform, so that is
    what this is: bounded on read-back (the last N lines of at most the
    last 64 KiB), deleted with the session.
    """

    def __init__(self) -> None:
        self._file = tempfile.TemporaryFile()

    def fileno(self) -> int:
        """The inherited handle the SDK passes to the child's spawn."""
        return self._file.fileno()

    @property
    def tail(self) -> list[str]:
        """The last lines the server wrote to stderr — report material."""
        try:
            size = os.fstat(self._file.fileno()).st_size
            self._file.seek(max(0, size - _STDERR_TAIL_BYTES))
            data = self._file.read()
        except (OSError, ValueError):
            return []
        lines = data.decode("utf-8", errors="replace").splitlines()
        return lines[-_STDERR_TAIL_LINES:]

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass


def _stdio_client(config: McpServerConfig, errlog: "_StderrLog") -> Any:
    """The SDK's stdio transport for *config*, logging the child to *errlog*.

    The environment is passed in full: the SDK merges its ``env`` over a
    trimmed platform default, and mocode's contract is the whole process
    environment plus the entry's overlay (with ``PLUGIN_ROOT`` /
    ``PLUGIN_DATA`` for plugin-sourced entries). Without this the child on
    Windows would see only the dozen variables the SDK keeps.
    """
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = dict(os.environ)
    env.update(config.env)
    if config.plugin_root is not None:
        env["PLUGIN_ROOT"] = str(config.plugin_root)
    if config.plugin_data is not None:
        env["PLUGIN_DATA"] = str(config.plugin_data)
    parameters = StdioServerParameters(
        command=config.command,
        args=list(config.args),
        env=env,
        cwd=config.cwd,
    )
    return stdio_client(parameters, errlog=errlog)


def _read_timeout(config: McpServerConfig) -> float:
    """The session's per-request budget — the entry's, else the default."""
    return config.timeout or _DEFAULT_REQUEST_TIMEOUT


@asynccontextmanager
async def _streamable_http_transport(
    config: McpServerConfig,
) -> AsyncIterator[Any]:
    """The SDK's streamable-HTTP transport for *config*.

    The transport itself takes neither headers nor a timeout, so both ride
    a pre-configured ``httpx2`` client: the entry's fixed headers become
    its defaults (the SDK's MCP headers take precedence on collision) and
    the read timeout is the session's per-request budget. The client is
    entered and left here because the SDK leaves a passed-in client's
    lifecycle to the caller.
    """
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    assert config.url is not None  # a parsed HTTP entry always has one
    async with httpx2.AsyncClient(
        headers=dict(config.headers),
        timeout=httpx2.Timeout(_HTTP_CONNECT_TIMEOUT, read=_read_timeout(config)),
    ) as http_client:
        async with streamable_http_client(
            config.url, http_client=http_client
        ) as streams:
            yield streams


def _sse_transport(config: McpServerConfig) -> Any:
    """The SDK's legacy SSE transport for *config* — unlike streamable
    HTTP it takes the headers and the timeouts itself; the SSE read budget
    is separate so an idle stream is not a failed request."""
    from mcp.client.sse import sse_client

    assert config.url is not None  # a parsed HTTP entry always has one
    return sse_client(
        config.url,
        headers=dict(config.headers),
        timeout=_read_timeout(config),
        sse_read_timeout=_SSE_READ_TIMEOUT,
    )


def _build_target(config: McpServerConfig, errlog: "_StderrLog") -> Any:
    """The SDK transport for *config*'s wire — the one place an entry
    becomes a connection target (decision D7).

    ``stdio`` spawns the child (its environment is explained on
    :func:`_stdio_client`); ``streamable-http`` is the current Streamable
    HTTP; ``sse`` is the legacy 2024-11-05 HTTP+SSE. All three negotiate
    the era through the same ``Client``, so era fallback comes for free.
    """
    if config.transport == "streamable-http":
        return _streamable_http_transport(config)
    if config.transport == "sse":
        return _sse_transport(config)
    return _stdio_client(config, errlog)


def _implementation() -> Any:
    """The client identity sent to every server, as the SDK's model."""
    from mcp.types import Implementation

    return Implementation(name=_CLIENT_INFO["name"], version=_CLIENT_INFO["version"])


@dataclass
class _Connection:
    """One connection attempt: the task serving the client, and its signals.

    ``ready`` is set once the client is negotiated (or the attempt failed,
    with the failure in ``error``); ``stop`` asks the task to leave the
    client — which kills the child. ``serving`` says whether the attempt got
    past the connect, so a sync shutdown knows whether cancelling the task
    would interrupt a spawn rather than an idle serve.
    """

    stop: asyncio.Event = field(default_factory=asyncio.Event)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: "asyncio.Task | None" = None
    error: McpError | None = None
    serving: bool = False


class McpSession:
    """One connection to one configured MCP server, over the official SDK.

    ``connect_and_register()`` connects under the caller's bounded wait,
    negotiates the era, lists the tools and hands them to the runtime;
    :meth:`list_tools` and :meth:`call_tool` speak whichever era was
    negotiated and answer with wire-form dicts; :meth:`close` and
    :meth:`shutdown` end the connection — the first by waiting for the
    connection task to unwind the client, the second by asking it to.
    ``on_connected``/``on_tools_changed`` are how the runtime learns about
    this session's tool list — this class never touches the registry.
    """

    def __init__(
        self,
        config: McpServerConfig,
        *,
        on_connected: (
            Callable[["McpSession", list[dict]], Awaitable[None]] | None
        ) = None,
        on_tools_changed: Callable[["McpSession"], Awaitable[None]] | None = None,
        server: Any = None,
    ):
        """*server* overrides the connection target the config describes —
        an in-process server (the tests' protocol seam) or a transport; the
        default builds the transport *config* names."""
        self.config = config
        self.name = config.name
        self.on_connected = on_connected
        self.on_tools_changed = on_tools_changed
        self._server = server

        self.era: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict = {}
        self.instructions: str = ""
        #: The server's advertised capabilities (SDK-typed), once connected.
        self.server_capabilities: "ServerCapabilities | None" = None
        self.state: str = STATE_DISCONNECTED
        self.last_error: str | None = None
        #: The most recent tools/list result — what the runtime registered.
        self.tools: list[dict] = []

        self._conn: _Connection | None = None
        self._client: "SdkClient | None" = None
        self._stderr: _StderrLog | None = None
        self._notify_tasks: set[asyncio.Task] = set()

    # ── introspection ───────────────────────────────────────

    @property
    def stderr_tail(self) -> list[str]:
        """The last lines the server wrote to stderr — report material."""
        return self._stderr.tail if self._stderr is not None else []

    # ── lifecycle ───────────────────────────────────────────

    async def connect_and_register(self) -> None:
        """The bounded-wait entry for direct servers: connect, list tools,
        hand them to the runtime through ``on_connected``."""
        await self._ensure_connection()
        tools = await self.list_tools()
        if self.on_connected is not None:
            await self.on_connected(self, tools)

    async def ensure_connected(self) -> None:
        """Reconnect after a drop; a closed session stays closed."""
        await self._ensure_connection()

    async def close(self) -> None:
        """Async teardown: ask the connection to stop and wait (bounded) for
        the client to be left — the SDK's shielded shutdown kills the child.
        """
        if self.state == STATE_CLOSED:
            return
        self.state = STATE_CLOSED
        await self._stop_connection()

    def shutdown(self) -> None:
        """Sync teardown for the plugin's ``close()`` — non-blocking.

        The connection task unwinds the client itself (its ``async with``
        body ends), so all a sync path can do is ask and, when the attempt
        is still mid-connect, cancel it: the SDK's shielded teardown kills
        the child either way.
        """
        if self.state == STATE_CLOSED:
            return
        self.state = STATE_CLOSED
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.stop.set()
        except RuntimeError:
            return  # the loop is gone; nothing can reap the teardown
        task = conn.task
        if not conn.serving and task is not None and not task.done():
            task.cancel()

    # ── MCP methods ─────────────────────────────────────────

    async def list_tools(self) -> list[dict]:
        """All tools the server offers, paging through ``nextCursor`` — as
        the wire-form dicts the runtime and the tool builders expect."""
        await self._ensure_connection()
        client = self._client
        assert client is not None  # ensured above
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            page = await self._request(lambda: client.list_tools(cursor=cursor))
            for tool in page.tools:
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": tool.input_schema,
                    }
                )
            cursor = page.next_cursor if page.next_cursor else None
            if not cursor:
                break
        self.tools = tools
        return tools

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call one tool by its raw MCP name; the wire-form result comes back.

        The SDK's built-in retry driver is not used: with no callbacks it can
        only refuse a server asking for input, and the manual seam
        (``allow_input_required=True``) says the same thing with an
        isinstance check instead of an exception heuristic — and an
        ``input_required`` answer is still the ``mcp_input_required``
        error, because mocode has no elicitation UI.
        """
        await self._ensure_connection()
        client = self._client
        assert client is not None  # ensured above
        result = await self._request(
            lambda: client.session.call_tool(
                name, arguments or {}, allow_input_required=True
            )
        )
        from mcp.types import InputRequiredResult

        if isinstance(result, InputRequiredResult):
            raise McpError(
                "server requires user input; elicitation is not supported",
                "mcp_input_required",
            )
        return result.model_dump(by_alias=True, exclude_none=True)

    async def list_resources(self, cursor: str | None = None) -> dict:
        """One page of the server's resources — wire-form, cursor and all."""
        await self._ensure_connection()
        client = self._client
        assert client is not None  # ensured above
        page = await self._request(lambda: client.list_resources(cursor=cursor))
        return page.model_dump(by_alias=True, exclude_none=True)

    async def list_resource_templates(self, cursor: str | None = None) -> dict:
        """One page of the server's resource templates — wire-form."""
        await self._ensure_connection()
        client = self._client
        assert client is not None  # ensured above
        page = await self._request(
            lambda: client.list_resource_templates(cursor=cursor)
        )
        return page.model_dump(by_alias=True, exclude_none=True)

    async def read_resource(self, uri: str) -> dict:
        """One resource by uri — the wire-form ``contents`` list."""
        await self._ensure_connection()
        client = self._client
        assert client is not None  # ensured above
        result = await self._request(lambda: client.read_resource(uri))
        return result.model_dump(by_alias=True, exclude_none=True)

    # ── the connection ──────────────────────────────────────

    async def _ensure_connection(self) -> None:
        """A live connection: start one if there is none — any task may make
        this call — and wait for its verdict, bounded by the caller (the
        runtime's connect timeout is what keeps a slow server from holding
        it)."""
        if self.state == STATE_CLOSED:
            raise McpError("session is closed", "mcp_closed")
        conn = self._conn
        if conn is not None and conn.task is not None and not conn.task.done():
            # an attempt is under way, or the connection is live: its verdict
            # covers this caller too.
            await self._await_connection(conn)
            return
        if self._client is not None:
            return
        conn = _Connection()
        conn.task = asyncio.create_task(self._serve(conn))
        self._conn = conn
        await self._await_connection(conn)

    async def _await_connection(self, conn: _Connection) -> None:
        """The attempt's verdict: a live client, or the failure it produced."""
        try:
            await conn.ready.wait()
        except BaseException:
            # This caller gave up (its own timeout or cancellation): the
            # attempt goes down with it, and the SDK's shielded teardown
            # reaps whatever child was spawned.
            conn.task.cancel()
            raise
        if conn.error is not None:
            raise conn.error

    async def _serve(self, conn: _Connection) -> None:
        """The connection's own task: enter the SDK client here, hold it
        while the session's calls use it, and leave it here too.

        Only the task that entered a client may leave it, and the teardown
        that kills the child runs on the way out — so this task is where
        the client lives, for as long as the connection lives.
        """
        stderr = _StderrLog()
        try:
            target = (
                self._server
                if self._server is not None
                else _build_target(self.config, stderr)
            )
            async with self._build_client(target) as client:
                self._client = client
                self._stderr = stderr
                self._absorb(client)
                self.state = STATE_CONNECTED
                self.last_error = None
                conn.serving = True
                conn.ready.set()
                await conn.stop.wait()
        except asyncio.CancelledError:
            conn.error = McpError("connection cancelled", "mcp_transport")
            conn.ready.set()
            raise
        except Exception as e:
            e = _unwrap(e)
            self.state = STATE_ERROR
            self.last_error = self._describe(e)
            conn.error = self._map(e)
            conn.ready.set()
        finally:
            self._client = None
            self._stderr = None
            stderr.close()
            self._cancel_notify_tasks()

    async def _stop_connection(self) -> None:
        """Ask the connection to stop and wait, bounded, for the unwind."""
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.stop.set()
        except RuntimeError:  # pragma: no cover - defensive
            return
        task = conn.task
        if task is None or task.done():
            return
        try:
            await asyncio.wait({task}, timeout=_EXIT_BOUND)
        finally:
            if not task.done():
                task.cancel()

    def _build_client(self, target: Any) -> "SdkClient":
        """The SDK client over *target* — auto negotiation, one input round
        allowed before it gives up, and every listing fetched from the
        server (no response cache) so a re-sync always sees a change."""
        from mcp import Client

        return Client(
            target,
            mode="auto",
            read_timeout_seconds=self.config.timeout or _DEFAULT_REQUEST_TIMEOUT,
            input_required_max_rounds=1,
            client_info=_implementation(),
            message_handler=self._on_message,
            cache=None,
        )

    def _absorb(self, client: "SdkClient") -> None:
        """Copy the negotiated facts off the SDK client — only readable
        inside its context, which is where they are read."""
        self.protocol_version = client.protocol_version
        self.era = (
            ERA_MODERN
            if self.protocol_version == _MODERN_VERSION
            else ERA_LEGACY
        )
        info = client.server_info
        if info is not None:
            self.server_info = {"name": info.name, "version": info.version}
        self.server_capabilities = client.server_capabilities
        self.instructions = client.instructions or ""

    async def _request(self, call: Callable[[], Any]) -> Any:
        """One SDK request on the live client — mapped onto the mocode-side
        errors, with a dropped connection marked so the next call reconnects.
        """
        try:
            return await call()
        except McpError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            e = _unwrap(e)
            if self._connection_lost(e):
                self._drop()
                raise McpError("server disconnected", "mcp_transport") from e
            error = self._map(e)
            if error.code == "mcp_timeout":
                self.last_error = str(e)
            raise error from e

    def _connection_lost(self, error: BaseException) -> bool:
        """Whether *error* means the connection is gone — the SDK turns a
        closed transport into an ``MCPError`` with ``CONNECTION_CLOSED``.
        """
        from mcp.shared.exceptions import MCPError
        from mcp_types import CONNECTION_CLOSED

        if isinstance(error, (ConnectionError, OSError, EOFError)):
            return True
        return isinstance(error, MCPError) and error.code == CONNECTION_CLOSED

    def _map(self, error: BaseException) -> McpError:
        """An SDK failure as a mocode-side error: a per-request timeout is
        ``mcp_timeout``, a server-side JSON-RPC error ``mcp_error``, and
        everything else — a spawn failure, an unparseable result — a
        transport failure. ``mcp_input_required`` comes from a result, not
        from an exception."""
        error = _unwrap(error)
        from mcp.shared.exceptions import MCPError
        from mcp_types import REQUEST_TIMEOUT

        if isinstance(error, MCPError):
            if error.code == REQUEST_TIMEOUT:
                return McpError(str(error), "mcp_timeout")
            return McpError(str(error), "mcp_error")
        if isinstance(error, TimeoutError):
            return McpError(str(error) or "request timed out", "mcp_timeout")
        return McpError(self._describe(error), "mcp_transport")

    def _describe(self, error: BaseException) -> str:
        """The reportable text for a failure — spawn failures name the
        command, exactly as the hand-written client did."""
        error = _unwrap(error)
        if isinstance(error, (OSError, ValueError)):
            return f"failed to start {self.config.command!r}: {error}"
        return str(error) or type(error).__name__

    def _drop(self) -> None:
        """Give up on a dead connection: its task is asked to stop (the
        client cannot be re-entered) and the next call starts a fresh one.
        The corpse is unwound by its own task; the session keeps only the
        reportable tail of it."""
        conn, self._conn = self._conn, None
        self._client = None
        self._stderr = None
        self.state = STATE_DISCONNECTED
        self.last_error = "server disconnected"
        if conn is not None:
            try:
                conn.stop.set()
            except RuntimeError:  # pragma: no cover - defensive
                pass

    # ── notifications ───────────────────────────────────────

    async def _on_message(self, message: Any) -> None:
        """The SDK's notification tap: a legacy ``tools/list_changed``
        re-syncs the tool set through the same callback a connect uses.
        Everything else goes nowhere on v1 — logging is captured as the
        stderr tail, and progress is not wired to the event stream."""
        from mcp.types import ToolListChangedNotification

        if (
            isinstance(message, ToolListChangedNotification)
            and self.on_tools_changed is not None
        ):
            task = asyncio.create_task(self.on_tools_changed(self))
            self._notify_tasks.add(task)
            task.add_done_callback(self._notify_tasks.discard)

    def _cancel_notify_tasks(self) -> None:
        for task in self._notify_tasks:
            task.cancel()
        self._notify_tasks.clear()

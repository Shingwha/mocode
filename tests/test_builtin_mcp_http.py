"""The mcp builtin plugin's HTTP transports — streamable HTTP and the
legacy SSE wire, against fake endpoints on the loopback port.

Every test starts a stdlib ``http.server`` endpoint bound to
``127.0.0.1`` port 0 in a daemon thread and answers the JSON-RPC wire
by hand, the way ``ref/mcp-protocol.md`` §10 prescribes — so nothing
here reaches the network. What is under test is what the plugin builds
out of an entry's ``transport`` / ``url`` / ``headers``: the target
:func:`_build_target` selects, the era negotiation riding it (the modern
probe, the legacy ``initialize`` fallback with its session), the SSE
response framing, and the fact that a configured header — an
Authorization token above all — really reaches the server. Every await
is bounded by ``BOUND`` so a broken fake can never hang the suite.
"""

from __future__ import annotations

import asyncio
import json
import queue
import socket
import threading
import time
from collections.abc import Iterator
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import pytest_asyncio

from mocode.host.plugin.builtin.mcp.client import (
    ERA_LEGACY,
    ERA_MODERN,
    STATE_CONNECTED,
    STATE_ERROR,
    McpError,
    McpSession,
)
from mocode.host.plugin.builtin.mcp.config import McpServerConfig

#: seconds — every session operation in this file stays bounded
BOUND = 15

#: One tool, enough to prove listing, calling and the response shape.
TOOLS = [
    {
        "name": "greet",
        "description": "Say hi",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }
]

#: The session id the legacy streamable-HTTP handshake hands out.
SESSION_ID = "legacy-session-7"
#: The endpoint event a legacy SSE server announces first — its session
#: id rides the query.
MESSAGE_PATH = "/messages?sessionId=sse-session-42"


def _result(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _discover_result() -> dict:
    """A modern ``server/discover`` answer — the SDK requires the result
    type, the supported versions and the cache fields at 2026-07-28."""
    return {
        "resultType": "complete",
        "supportedVersions": ["2026-07-28"],
        "capabilities": {"tools": {}},
        "_meta": {
            "io.modelcontextprotocol/serverInfo": {"name": "http-srv", "version": "1.0"}
        },
        "instructions": "HTTP server instructions.",
        "ttlMs": 0,
        "cacheScope": "public",
    }


def _initialize_result() -> dict:
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "http-legacy-srv", "version": "1.0"},
        "instructions": "Legacy HTTP server instructions.",
    }


def _tools_list_result(*, modern: bool) -> dict:
    result: dict[str, Any] = {"tools": TOOLS}
    if modern:
        result.update({"resultType": "complete", "ttlMs": 0, "cacheScope": "public"})
    return result


def _call_result(message: dict, *, modern: bool) -> dict:
    arguments = (message.get("params") or {}).get("arguments") or {}
    text = f"hi {arguments.get('name', '')}"
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"text": text},
    }
    if modern:
        result["resultType"] = "complete"
    return result


class _FakeServer(ThreadingHTTPServer):
    """A threaded stdlib server that knows which fake it serves."""

    fake: _FakeEndpoint


class _FakeEndpoint:
    """A fake MCP endpoint on a loopback port, served in a daemon thread.

    ``style`` picks the wire the fake speaks:

    * ``json`` — streamable HTTP, modern era, every response a JSON body;
    * ``sse`` — streamable HTTP, modern era, every response an SSE frame;
    * ``legacy`` — streamable HTTP that answers the modern probe with a
      plain error (so the client falls back to the ``initialize``
      handshake), hands out a ``Mcp-Session-Id`` and keeps the optional
      GET stream open;
    * ``sse-transport`` — the legacy 2024-11-05 HTTP+SSE wire: a GET
      stream that announces the POST endpoint first, with every answer
      riding the stream.

    Every request is recorded (path, headers, message) so a test can
    assert what really reached the server.
    """

    def __init__(self, *, style: str) -> None:
        assert style in ("json", "sse", "legacy", "sse-transport")
        self.style = style
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stream: queue.Queue[tuple[str, str]] = queue.Queue()
        self._stopped = False
        self._server = _FakeServer(("127.0.0.1", 0), partial(_Handler))
        self._server.fake = self
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        path = "/sse" if self.style == "sse-transport" else "/mcp"
        return f"http://127.0.0.1:{self._server.server_address[1]}{path}"

    def start(self) -> _FakeEndpoint:
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopped = True
        self._server.shutdown()
        self._server.server_close()

    # ── what the handler threads share ─────────────────────

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def stream(self) -> queue.Queue[tuple[str, str]]:
        """Frames waiting to be written to the legacy SSE stream."""
        return self._stream

    def record(self, path: str, headers: Any, message: dict | None) -> None:
        with self._lock:
            self.requests.append(
                {
                    "path": path,
                    "headers": {str(k).lower(): str(v) for k, v in headers.items()},
                    "message": message,
                }
            )

    def answer(self, message: dict) -> dict:
        """The JSON-RPC reply for one request *message*."""
        method, rid = message.get("method"), message.get("id")
        if method == "server/discover" and self.style in ("json", "sse"):
            return _result(rid, _discover_result())
        if method == "initialize":
            return _result(rid, _initialize_result())
        if method == "tools/list":
            return _result(rid, _tools_list_result(modern=self.style in ("json", "sse")))
        if method == "tools/call":
            return _result(rid, _call_result(message, modern=self.style in ("json", "sse")))
        # a legacy server answers the modern probe with a plain error,
        # which is exactly what makes the client fall back to the handshake
        return _error(rid, -32601, f"method not found: {method}")


class _Handler(BaseHTTPRequestHandler):
    """One request against the fake: JSON, SSE-framed, or SSE transport."""

    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:
        pass  # keep the test output quiet

    @property
    def fake(self) -> _FakeEndpoint:
        return self.server.fake  # type: ignore[attr-defined]

    # ── writers ────────────────────────────────────────────

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_frame(self, event: str, data: str) -> None:
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    # ── verbs ──────────────────────────────────────────────

    def do_GET(self) -> None:
        fake = self.fake
        if fake.style == "sse-transport" and self.path.startswith("/sse"):
            self._serve_stream()
        elif fake.style == "legacy" and self.path.startswith("/mcp"):
            self._serve_legacy_get_stream()
        else:
            self.send_error(404)

    def _serve_stream(self) -> None:
        """The legacy SSE transport's GET stream: announce the POST
        endpoint (its session id rides the query), then forward every
        answer the POST side queues — until the client goes away."""
        fake = self.fake
        fake.record(self.path, self.headers, None)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self._send_frame("endpoint", MESSAGE_PATH)
            while True:
                try:
                    event, data = fake.stream.get(timeout=0.2)
                except queue.Empty:
                    if fake.stopped:
                        return
                    continue
                self._send_frame(event, data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _serve_legacy_get_stream(self) -> None:
        """A streamable-HTTP server's optional legacy GET stream: open,
        kept alive with comments until the client hangs up."""
        fake = self.fake
        fake.record(self.path, self.headers, None)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            while not fake.stopped:
                self.wfile.write(b": keep-alive\n\n")
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self) -> None:
        fake = self.fake
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            message = json.loads(body)
        except ValueError:
            self.send_error(400)
            return
        fake.record(self.path, self.headers, message)
        if "id" not in message:
            # a notification: accepted, and nothing comes back
            self._send(202, b"", "text/plain")
            return
        reply = fake.answer(message)
        if fake.style == "sse-transport":
            # the answer rides the GET stream, the POST is only receipt
            fake.stream.put(("message", json.dumps(reply)))
            self._send(202, b"", "text/plain")
            return
        if fake.style == "sse":
            # the same answer, framed as one SSE event
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self._send_frame("message", json.dumps(reply))
            return
        extra = (
            {"Mcp-Session-Id": SESSION_ID}
            if fake.style == "legacy" and message.get("method") == "initialize"
            else None
        )
        self._send(200, json.dumps(reply).encode("utf-8"), "application/json", extra)

    def do_DELETE(self) -> None:
        """The streamable-HTTP transport's session termination."""
        self.fake.record(self.path, self.headers, None)
        self._send(204, b"", "text/plain")


# ── the seams ─────────────────────────────────────────


def http_config(
    url: str,
    *,
    transport: str = "streamable-http",
    headers: dict[str, str] | None = None,
    name: str = "demo",
) -> McpServerConfig:
    """A session config for an HTTP *url* — the fields :mod:`.config`
    parses out of an entry, assembled as directly as a test can."""
    return McpServerConfig(
        name=name,
        transport=transport,
        url=url,
        headers=headers or {},
        source="test",
    )


def _refused_url() -> str:
    """A loopback URL with nothing behind it — a port just vacated."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/mcp"


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch: Any) -> None:
    """Keep every request on the loopback: a system proxy (Windows reads
    one from the registry) would otherwise answer for the fake endpoints
    and for a dead port alike, making the outcome machine-dependent and
    letting a request leave the machine at all."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")


@pytest.fixture
def endpoint() -> Iterator[Any]:
    """Start fake endpoints on demand; every one is torn down after."""
    started: list[_FakeEndpoint] = []

    def factory(style: str) -> _FakeEndpoint:
        fake = _FakeEndpoint(style=style).start()
        started.append(fake)
        return fake

    yield factory
    for fake in started:
        fake.stop()


@pytest_asyncio.fixture
async def session_factory() -> Any:
    """Build McpSessions over HTTP configs; close them all on teardown."""
    created: list[McpSession] = []

    def factory(cfg: McpServerConfig, **kwargs) -> McpSession:
        session = McpSession(cfg, **kwargs)
        created.append(session)
        return session

    yield factory
    for session in created:
        try:
            await asyncio.wait_for(session.close(), BOUND)
        except Exception:
            session.shutdown()


# ── streamable HTTP ───────────────────────────────────


class TestStreamableHttp:
    async def test_modern_json_responses_reach_the_session(self, endpoint, session_factory):
        fake = endpoint("json")
        session = session_factory(http_config(fake.url))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert session.server_info == {"name": "http-srv", "version": "1.0"}
        assert session.instructions == "HTTP server instructions."
        assert session.state == STATE_CONNECTED
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(session.call_tool("greet", {"name": "mocode"}), BOUND)
        assert result["content"] == [{"type": "text", "text": "hi mocode"}]
        assert result["structuredContent"] == {"text": "hi mocode"}

    async def test_sse_framed_responses_are_parsed(self, endpoint, session_factory):
        fake = endpoint("sse")
        session = session_factory(http_config(fake.url))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(session.call_tool("greet", {"name": "sse"}), BOUND)
        assert result["content"] == [{"type": "text", "text": "hi sse"}]
        assert result["structuredContent"] == {"text": "hi sse"}

    async def test_configured_headers_reach_the_server(self, endpoint, session_factory):
        fake = endpoint("json")
        session = session_factory(
            http_config(
                fake.url,
                headers={"Authorization": "Bearer test-token", "X-Mcp-Test": "yes"},
            )
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        await asyncio.wait_for(session.call_tool("greet", {"name": "mocode"}), BOUND)
        posts = [r for r in fake.requests if r["message"] is not None]
        assert posts
        for request in posts:
            assert request["headers"].get("authorization") == "Bearer test-token"
            assert request["headers"].get("x-mcp-test") == "yes"


class TestLegacyOverStreamableHttp:
    async def test_the_failed_probe_falls_back_to_the_handshake(self, endpoint, session_factory):
        fake = endpoint("legacy")
        session = session_factory(http_config(fake.url))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "http-legacy-srv", "version": "1.0"}
        assert session.instructions == "Legacy HTTP server instructions."
        posts = [r for r in fake.requests if r["message"] is not None]
        assert [r["message"].get("method") for r in posts[:2]] == [
            "server/discover",
            "initialize",
        ]
        # the session the handshake was given rides every later request
        later = posts[2:]
        assert later
        for request in later:
            assert request["headers"].get("mcp-session-id") == SESSION_ID


# ── the legacy SSE transport ──────────────────────────


class TestSseTransport:
    async def test_the_handshake_rides_the_stream_and_keeps_the_session(
        self, endpoint, session_factory
    ):
        fake = endpoint("sse-transport")
        session = session_factory(
            http_config(
                fake.url,
                transport="sse",
                headers={"Authorization": "Bearer sse-token"},
            )
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(session.call_tool("greet", {"name": "sse"}), BOUND)
        assert result["content"] == [{"type": "text", "text": "hi sse"}]
        gets = [r for r in fake.requests if r["message"] is None]
        posts = [r for r in fake.requests if r["message"] is not None]
        assert gets and gets[0]["path"] == "/sse"
        # configured headers reach the GET stream as well as the POSTs
        assert gets[0]["headers"].get("authorization") == "Bearer sse-token"
        # the endpoint event's session id rides every message POST
        assert posts and all("sessionId=sse-session-42" in r["path"] for r in posts)


# ── failures ──────────────────────────────────────────


class TestConnectionFailures:
    async def test_a_refused_connection_is_a_transport_error(self, session_factory):
        session = session_factory(http_config(_refused_url()))
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert err.value.code == "mcp_transport"
        assert session.state == STATE_ERROR
        assert session.last_error

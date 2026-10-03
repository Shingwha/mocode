"""Shared fakes for the mcp tests — the wire peer and the script helpers.

Two seams, one per job. **WirePeer** is a scripted JSON-RPC server over the
SDK's in-memory streams: the protocol-shape seam, used by
``test_builtin_mcp.py`` and the mcp group of ``test_builtin_codemode.py``
so that era negotiation, wire-form results and error mapping are proved
without a child process. The script helpers — writing a fake server, an
mcp.json, a stdio entry and the pidfile env — are what
``test_builtin_mcp_process.py`` uses for the real-subprocess contract.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import mcp_types as sdk_types
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage

#: A minimal modern-era MCP server with one tool, ``echo``, that answers
#: ``tools/call`` with its arguments serialized as JSON.
ECHO_SERVER = r'''
import json, sys, os

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

pidfile = os.environ.get("MCP_TEST_PIDFILE")
if pidfile:
    open(pidfile, "w").write(str(os.getpid()))
sys.stderr.write("echo server starting\n")
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
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "resultType": "complete",
            "content": [{"type": "text", "text": "echo:" + json.dumps(params.get("arguments") or {})}],
            "structuredContent": {"args": params.get("arguments") or {}}}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''


def write_server(tmp_path: Path, name: str, code: str = ECHO_SERVER) -> Path:
    """Write a fake server script into *tmp_path*; return its path."""
    path = tmp_path / name
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return path


def write_mcp_json(path: Path, servers: dict) -> Path:
    """Write an ``mcp.json`` holding the given ``mcpServers`` table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def stdio_entry(script: Path, **extra) -> dict:
    """A stdio server entry pointing at a fake script."""
    return {"type": "stdio", "command": sys.executable, "args": [str(script)], **extra}


def pidfile_env(tmp_path: Path, name: str) -> dict:
    """The env naming the fake's pidfile, so a test can watch its child."""
    return {"MCP_TEST_PIDFILE": str(tmp_path / f"{name}.pid")}


async def _peer_timer(seconds: float) -> None:
    """How the fake peer waits out a scripted delay.

    A slow server's latency is the behaviour under test here — the same job
    the subprocess fakes' own ``time.sleep`` did — so the wait goes through
    :func:`settle`, the sanctioned entry for exactly that.
    """
    await settle(seconds)

class ByToolName:
    """A route that answers by the tool the request names."""

    def __init__(self, answers) -> None:
        self.answers = answers


class Unsupported:
    """A ``-32022`` discover refusal naming the versions the peer speaks."""

    def __init__(self, supported: list[str]) -> None:
        self.supported = supported


class Late:
    """A route answered after a delay — the slow-call timeout's shape."""

    def __init__(self, seconds: float, result: dict) -> None:
        self.seconds = seconds
        self.result = result


class Drop:
    """A route that closes the stream instead of answering — a dropped peer."""


class Silent:
    """A route that never answers — a peer that hangs up on nothing."""


class WirePeer:
    """A scripted JSON-RPC server over the SDK's in-memory streams.

    ``routes`` maps a method to what it answers: a ``dict`` result,
    :class:`Late` for a delayed one, :class:`Silent` for no answer at all,
    :class:`Unsupported` for the ``-32022`` era refusal, :class:`Drop` to
    close the stream underneath the client. An unlisted method answers
    ``-32601`` — which is exactly what makes a pre-discover server fall
    back to the handshake. ``seen`` records the methods that arrived, so a
    test asserts on the requests that were really made.
    """

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.seen: list[str] = []

    async def _serve(self, read, write) -> None:
        while True:
            message = await read.receive()
            body = message.message
            if isinstance(body, sdk_types.JSONRPCNotification):
                self.seen.append(body.method)
                continue
            assert isinstance(body, sdk_types.JSONRPCRequest), body
            method, rid = body.method, body.id
            self.seen.append(method)
            out = self.routes.get(method, "unknown")
            if out == "unknown":
                await write.send(
                    SessionMessage(
                        sdk_types.JSONRPCError(
                            jsonrpc="2.0",
                            id=rid,
                            error=sdk_types.ErrorData(
                                code=-32601, message=f"unknown method {method}"
                            ),
                        )
                    )
                )
                continue
            if isinstance(out, Silent):
                continue
            if isinstance(out, ByToolName):
                out = out.answers[body.params.get("name")]
            if isinstance(out, Late):
                # the delay is the behaviour under test (a slow server), so
                # the peer waits on a real timer — and the guard's caller
                # frame is this fake, not the test that drives it.
                await _peer_timer(out.seconds)
                out = out.result
            if isinstance(out, Drop):
                await write.aclose()
                return
            if isinstance(out, Unsupported):
                await write.send(
                    SessionMessage(
                        sdk_types.JSONRPCError(
                            jsonrpc="2.0",
                            id=rid,
                            error=sdk_types.ErrorData(
                                code=-32022,
                                message="unsupported version",
                                data={
                                    "supported": out.supported,
                                    "requested": "2026-07-28",
                                },
                            ),
                        )
                    )
                )
                continue
            await write.send(
                SessionMessage(
                    sdk_types.JSONRPCResponse(jsonrpc="2.0", id=rid, result=out)
                )
            )

    async def __aenter__(self):
        self._cm = create_client_server_memory_streams()
        self._streams = await self._cm.__aenter__()
        self._task = asyncio.ensure_future(self._serve(*self._streams[1]))
        return self._streams[0]

    async def __aexit__(self, *exc) -> bool:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        await self._cm.__aexit__(*exc)
        return False

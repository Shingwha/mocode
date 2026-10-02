"""Small fakes for the cross-plugin MCP + codemode tests.

Same pattern as ``test_builtin_mcp.py``: the server is a Python script
written into ``tmp_path`` and launched with ``sys.executable`` — no shell.
This one speaks the modern protocol era with a single ``echo`` tool, which
is all the integration tests need; the richer fakes (legacy era,
negotiation, paging, failures) stay in the W1 test file, untouched.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

#: A minimal modern-era MCP server with one tool, ``echo``, that answers
#: ``tools/call`` with its arguments serialized as JSON.
ECHO_SERVER = r'''
import json, sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

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
        send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": TOOLS}})
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

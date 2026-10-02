"""JSON-RPC 2.0 framing for MCP stdio — newline-delimited JSON.

Every message is one JSON object on a single UTF-8 line terminated by
``"\n"``; ``json.dumps(..., ensure_ascii=False)`` never emits a bare newline
inside a message, so one line is always one message. The read side skips
empty lines and hands unparseable lines back as ``None`` — a transport
glitch is reported and dropped, never fatal to the connection.
"""

from __future__ import annotations

import json

JSONRPC_VERSION = "2.0"


def encode_message(message: dict) -> bytes:
    """One JSON-RPC message as a single newline-terminated UTF-8 line."""
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(line: bytes) -> dict | None:
    """Parse one incoming line.

    Returns the message when it is a JSON object, else ``None`` (a bare
    acknowledgement of garbage — the caller reports and drops it).
    """
    try:
        message = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return message if isinstance(message, dict) else None

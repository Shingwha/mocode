"""Output for codemode — collection, rendering, truncation, temp files.

A script's visible product is a list of text items plus image blocks. The
model sees only the composed text — the header line, the (possibly
truncated) body, and, on failure, the error line — while the image blocks
ride in the result's ``details``. When the body outgrows ``max_output_chars``
the middle is replaced with an omission marker, the full text lands in a
temp file, and the result points at it, so a script can pour megabytes
through the filter without flooding the conversation.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from .....core.tool import ToolResult
from .runtime import script_error_line

__all__ = ["Output", "build_result", "compose", "truncate_body"]


class Output:
    """What a script emitted: text items and image blocks."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self.images: list[dict] = []

    def text(self, value) -> None:
        """Append one item — strings as-is, everything else as JSON."""
        if isinstance(value, str):
            self.items.append(value)
        else:
            self.items.append(json.dumps(value, default=str))

    def image(self, block) -> None:
        """Append an image block; the body gets a ``[image: mime]`` marker."""
        self.images.append(block)
        self.items.append(f"[image: {_image_mime(block)}]")

    def render_body(self) -> str:
        return "\n".join(self.items)


def _image_mime(block) -> str:
    """Best-effort mime of an image block — MCP-style dict, data URL or
    ``{image_url}`` object; unknown shapes render as plain ``image``."""
    if isinstance(block, str) and block.startswith("data:"):
        return block[5:].split(";", 1)[0] or "image"
    if isinstance(block, dict):
        mime = block.get("mimeType") or block.get("mime")
        if mime:
            return mime
        url = block.get("image_url")
        if isinstance(url, str) and url.startswith("data:"):
            return url[5:].split(";", 1)[0] or "image"
    return "image"


def truncate_body(body: str, max_chars: int) -> tuple[str, str | None]:
    """Bound *body* to *max_chars* with head+tail truncation.

    The first and last ``max_chars // 2`` characters survive, separated by
    an ``…<n> chars truncated…`` marker counting the omitted characters; the
    full text is written to ``<tmp>/mocode-codemode-<uuid>.txt`` (UTF-8) and
    its path returned. ``max_chars`` at or below zero, or a body that
    already fits, comes back unchanged with no file.
    """
    if max_chars <= 0 or len(body) <= max_chars:
        return body, None
    half = max_chars // 2
    head, tail = body[:half], body[len(body) - half:] if half else ""
    omitted = len(body) - len(head) - len(tail)
    path = Path(tempfile.gettempdir()) / f"mocode-codemode-{uuid.uuid4().hex}.txt"
    path.write_text(body, encoding="utf-8")
    return head + f"\n…{omitted} chars truncated…\n" + tail, str(path)


def _source_line(script: str, line: int) -> str | None:
    """The trimmed *line*-th line of *script* (1-based), or ``None`` when the
    index falls outside the script."""
    lines = script.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return None


def compose(
    ok: bool,
    ms: int,
    body: str,
    error: BaseException | None,
    full_output_path: str | None,
    *,
    script: str = "",
) -> str:
    """The model-facing text: status line, body, error line, temp-file path.

    An empty body leaves no blank line — success is just the status line,
    failure is the status line plus the error line. A failure located in the
    script's own code (see
    :func:`~mocode.host.plugin.builtin.codemode.runtime.script_error_line`)
    names the script line and shows that line's source on the next line;
    anything else keeps the plain ``Script error:`` format.
    """
    text = f"Script {'completed' if ok else 'failed'} in {ms}ms"
    if body:
        text += f"\n{body}"
    if not ok and error is not None:
        line = script_error_line(error)
        if line is None:
            text += f"\nScript error: {type(error).__name__}: {error}"
        else:
            text += f"\nScript error (line {line}): {type(error).__name__}: {error}"
            source = _source_line(script, line)
            if source is not None:
                text += f"\n{source}"
    if full_output_path:
        text += f"\nFull output: {full_output_path}"
    return text


def build_result(
    *,
    ok: bool,
    ms: int,
    output: Output,
    error: BaseException | None,
    tool_calls: int,
    max_chars: int,
    script: str = "",
    timed_out: bool = False,
) -> ToolResult:
    """The codemode tool's ToolResult — content for the model, facts in details.

    ``timed_out`` is set only when the plugin's own deadline fired: the
    details then carry the ``"timed_out": True`` marker alongside the
    partial output in the content."""
    body, path = truncate_body(output.render_body(), max_chars)
    details = {
        "ok": ok,
        "images": list(output.images),
        "truncated": path is not None,
        "full_output_path": path,
        "tool_calls": tool_calls,
    }
    if timed_out:
        details["timed_out"] = True
    return ToolResult(
        content=compose(ok, ms, body, error, path, script=script),
        details=details,
    )

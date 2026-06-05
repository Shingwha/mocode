"""File operation tools — ReadTool, WriteTool, AppendTool, EditTool."""

from __future__ import annotations

from pathlib import Path

from ..core.tool import Tool, ToolError
from ..core.virtualfs import VirtualFS
from ._helpers import read_bytes, require_file


def _format_lines(content: str, label: str, offset: int, limit: int) -> str:
    """Format *content* with line numbers, matching the read tool output format.

    *label* is shown in the header (file path or vfs:// URI).
    *offset* is 1-based.  *limit* = 0 means all lines.
    """
    all_lines = content.splitlines(keepends=True)
    total = len(all_lines)
    size_kb = len(content.encode("utf-8")) / 1024

    start = max(0, offset - 1)
    end = start + limit if limit else total
    selected = all_lines[start:end]

    if not selected:
        raise ToolError(
            f"Line {offset} is beyond end of file (file has {total} lines)",
            "out_of_range",
        )

    header = f"[{label} | {total} lines | {size_kb:.1f} KB]"
    lines_text = "".join(
        f"{start + idx + 1:>5} | {line}" for idx, line in enumerate(selected)
    )

    end_line = start + len(selected)
    if end_line < total:
        footer = f"\n[Showing lines {start + 1}-{end_line} of {total}. Use offset={end_line + 1} to read more.]"
    else:
        footer = ""

    return header + "\n" + lines_text + footer


_READ_PARAMS = {
    "path": {"type": "string", "description": "File path to read"},
    "offset": {
        "type": "integer",
        "description": "Line number to start from (1-based, default 1)",
        "default": 1,
    },
    "limit": {
        "type": "integer",
        "description": "Max lines to read (0 = all lines)",
        "default": 0,
    },
}

_READ_DESC = (
    "Read a file and return its contents with line numbers. "
    "Supports text files with UTF-8/GBK encoding. "
    "Use offset and limit to read specific line ranges. Line numbers are 1-based. "
    "The output includes file metadata (total lines, size) and truncation info when the file is too long."
)


def _read_text(p: Path, offset: int, limit: int) -> str:
    """Read a real file with line numbers."""
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ToolError(f"Cannot read file: {e}", "read_error")
    if b"\x00" in raw[:8192]:
        raise ToolError(f"File appears to be binary: {p}", "binary_file")
    content = read_bytes(raw)
    return _format_lines(content, str(p), offset, limit)


def ReadTool(vfs: VirtualFS | None = None) -> Tool:
    """Create a read tool."""

    def _read(args: dict) -> str:
        path = args["path"]
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", 0)) or 999999

        if vfs and vfs.exists(path):
            return _format_lines(vfs.get(path), path, offset, limit)

        p = require_file(Path(path))
        return _read_text(p, offset, limit)

    return Tool("read", _READ_DESC, _READ_PARAMS, _read)


def _write(args: dict) -> str:
    p = Path(args["path"])
    if p.is_dir():
        raise ToolError(f"Path is a directory: {p}", "invalid_path")
    content = args["content"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    line_count = content.count("\n") + (
        1 if content and not content.endswith("\n") else 0
    )
    return f"Wrote {line_count} lines to {p.name}"


def _append(args: dict) -> str:
    p = Path(args["path"])
    if p.is_dir():
        raise ToolError(f"Path is a directory: {p}", "invalid_path")

    content = args["content"]
    if p.exists():
        existing = p.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            content = "\n" + content

    with open(p, "a", encoding="utf-8") as f:
        f.write(content)
    line_count = content.count("\n") + (
        1 if content and not content.endswith("\n") else 0
    )
    return f"Appended {line_count} lines to {p.name}"


def _edit(args: dict) -> str:
    p = require_file(Path(args["path"]))

    text = p.read_text(encoding="utf-8")
    old, new = args["old_string"], args["new_string"]

    if old not in text:
        raise ToolError("old_string not found in file", "not_found")

    count = text.count(old)
    replace_all = args.get("all", False)
    if not replace_all and count > 1:
        raise ToolError(
            f"old_string appears {count} times, must be unique (use all=true)",
            "not_unique",
        )

    replacement = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    p.write_text(replacement, encoding="utf-8")
    actual = count if replace_all else 1
    return f"Replaced {actual} occurrence(s) in {p.name}"


def WriteTool() -> Tool:
    return Tool(
        "write",
        "Write content to a file. Creates the file and any parent directories if they don't exist. "
        "Overwrites existing content entirely. For appending, use the append tool instead.",
        {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Content to write (UTF-8)"},
        },
        _write,
    )


def AppendTool() -> Tool:
    return Tool(
        "append",
        "Append content to the end of a file. Creates the file if it doesn't exist. "
        "Automatically adds a newline before the content if the existing file doesn't end with one.",
        {
            "path": {"type": "string", "description": "File path to append to"},
            "content": {"type": "string", "description": "Content to append (UTF-8)"},
        },
        _append,
    )


def EditTool() -> Tool:
    return Tool(
        "edit",
        "Find and replace text in a file. The old_string must match exactly (including whitespace and indentation). "
        "By default, old_string must appear exactly once in the file — the tool will fail if it matches multiple locations. "
        "Use all=true to replace every occurrence. The file must already exist.",
        {
            "path": {"type": "string", "description": "File path to edit"},
            "old_string": {
                "type": "string",
                "description": "Exact text to find (must be unique unless all=true)",
            },
            "new_string": {"type": "string", "description": "Replacement text"},
            "all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of just the first",
                "default": False,
            },
        },
        _edit,
    )

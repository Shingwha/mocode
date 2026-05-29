"""File operation tools — ReadTool, WriteTool, AppendTool, EditTool."""

from __future__ import annotations

from pathlib import Path

from ..core.tool import Tool, ToolError
from ._helpers import read_text, require_file


def _read_text(p: Path, offset: int, limit: int) -> str:
    """Read text file with line numbers. offset is 1-based."""
    # Binary detection
    try:
        chunk = p.read_bytes()[:8192]
        if b"\x00" in chunk:
            raise ToolError(f"File appears to be binary: {p}", "binary_file")
    except OSError as e:
        raise ToolError(f"Cannot read file: {e}", "read_error")

    # Read with encoding fallback
    all_lines = read_text(p).splitlines(keepends=True)

    total = len(all_lines)
    size_kb = p.stat().st_size / 1024

    # Convert 1-based offset to 0-based index, clamp to valid range
    start = max(0, offset - 1)
    end = start + limit if limit else total
    selected = all_lines[start:end]

    if not selected:
        raise ToolError(
            f"Line {offset} is beyond end of file (file has {total} lines)",
            "out_of_range",
        )

    header = f"[{p} | {total} lines | {size_kb:.1f} KB]"
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
    "offset": {"type": "integer", "description": "Line number to start from (1-based, default 1)", "default": 1},
    "limit": {"type": "integer", "description": "Max lines to read (0 = all lines)", "default": 0},
}

_READ_DESC = (
    "Read a file and return its contents with line numbers. "
    "Supports text files with UTF-8/GBK encoding. "
    "Use offset and limit to read specific line ranges. Line numbers are 1-based. "
    "The output includes file metadata (total lines, size) and truncation info when the file is too long."
)


def ReadTool() -> Tool:
    """Create a read tool."""

    def _read(args: dict) -> str:
        p = require_file(Path(args["path"]))
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", 0)) or 999999
        return _read_text(p, offset, limit)

    return Tool("read", _READ_DESC, _READ_PARAMS, _read)


def _write(args: dict) -> str:
    p = Path(args["path"])
    if p.is_dir():
        raise ToolError(f"Path is a directory: {p}", "invalid_path")
    content = args["content"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
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
    line_count = content.strip("\n").count("\n") + 1
    return f"Appended {line_count} lines to {p.name}"


def _edit(args: dict) -> str:
    p = require_file(Path(args["path"]))

    text = p.read_text(encoding="utf-8")
    old, new = args["old"], args["new"]

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
            "old": {"type": "string", "description": "Exact text to find (must be unique unless all=true)"},
            "new": {"type": "string", "description": "Replacement text"},
            "all": {"type": "boolean", "description": "Replace all occurrences instead of just the first", "default": False},
        },
        _edit,
    )

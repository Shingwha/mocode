"""File operation tools — ReadTool, WriteTool, AppendTool, EditTool."""

from __future__ import annotations

import base64
from pathlib import Path

from ..core.hook import AgentHook, AgentHookContext
from ..core.tool import Tool, ToolError

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_EXT_TO_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class ImageInjectionHook(AgentHook):
    """Hook for ReadTool to inject image messages after tool execution."""

    def __init__(self):
        self._pending: list[dict] = []

    def add_pending(self, msg: dict) -> None:
        self._pending.append(msg)

    async def after_tools(self, ctx: AgentHookContext) -> None:
        if self._pending:
            ctx.messages.extend(list(self._pending))
            self._pending.clear()


def _read_text(p: Path, offset: int, limit: int) -> str:
    try:
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        lines = p.read_text(encoding="gbk", errors="replace").splitlines(keepends=True)
    selected = lines[offset : offset + limit]
    return "".join(f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected))


def ReadTool(image_hook: ImageInjectionHook | None = None) -> Tool:
    """Create a read tool. Pass image_hook to enable image reading."""

    if image_hook is None:
        # Pure text mode
        def _read(args: dict) -> str:
            p = Path(args["path"])
            if not p.exists():
                raise ToolError(f"File not found: {p}", "file_not_found")
            if p.is_dir():
                raise ToolError(f"Path is a directory: {p}", "invalid_path")
            offset = int(args.get("offset", 0))
            limit = int(args.get("limit", 0)) or 999999
            return _read_text(p, offset, limit)

    else:
        # Image-capable mode — inject images via hook
        def _read(args: dict) -> str:
            p = Path(args["path"])
            if not p.exists():
                raise ToolError(f"File not found: {p}", "file_not_found")
            if p.is_dir():
                raise ToolError(f"Path is a directory: {p}", "invalid_path")

            if p.suffix.lower() in IMAGE_EXTS:
                try:
                    b64 = base64.b64encode(p.read_bytes()).decode()
                except OSError as e:
                    raise ToolError(f"Failed to read image: {e}", "read_error")

                media_type = _EXT_TO_MEDIA[p.suffix.lower()]
                image_hook.add_pending({
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                        {"type": "text", "text": f"[Image loaded: {p.name}]"},
                    ],
                })
                size_kb = p.stat().st_size / 1024
                return f"Image loaded from {p} ({size_kb:.1f} KB, {media_type})"

            offset = int(args.get("offset", 0))
            limit = int(args.get("limit", 0)) or 999999
            return _read_text(p, offset, limit)

    return Tool(
        "read",
        "Read a file. For text files, returns content with line numbers. "
        "For image files (png/jpg/jpeg/gif/webp/bmp), attaches the image to the conversation for visual analysis.",
        {"path": "string", "offset": "number?", "limit": "number?"},
        _read,
    )


def _write(args: dict) -> str:
    p = Path(args["path"])
    if p.is_dir():
        raise ToolError(f"Path is a directory: {p}", "invalid_path")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"], encoding="utf-8")
    return "ok"


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
    return "ok"


def _edit(args: dict) -> str:
    p = Path(args["path"])
    if not p.exists():
        raise ToolError(f"File not found: {p}", "file_not_found")

    text = p.read_text(encoding="utf-8")
    old, new = args["old"], args["new"]

    if old not in text:
        raise ToolError("old_string not found", "not_found")

    count = text.count(old)
    if not args.get("all") and count > 1:
        raise ToolError(
            f"old_string appears {count} times, must be unique (use all=true)",
            "not_unique",
        )

    replacement = text.replace(old, new) if args.get("all") else text.replace(old, new, 1)
    p.write_text(replacement, encoding="utf-8")
    return "ok"


WriteTool = Tool(
    "write",
    "Write content to file",
    {"path": "string", "content": "string"},
    _write,
)

AppendTool = Tool(
    "append",
    "Append content to file (creates if not exists)",
    {"path": "string", "content": "string"},
    _append,
)

EditTool = Tool(
    "edit",
    "Replace old with new in file (old must be unique unless all=true)",
    {"path": "string", "old": "string", "new": "string", "all": "boolean?"},
    _edit,
)

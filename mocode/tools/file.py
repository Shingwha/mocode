"""文件操作工具 — 工厂函数注册模式

v0.2 关键改进：config 通过闭包捕获，不需要 ContextVar。
"""

from pathlib import Path

from ..tool import Tool, ToolError, ToolRegistry
from ..config import Config


def register_file_tools(registry: ToolRegistry, config: Config) -> None:
    """注册文件操作工具"""

    def _read(args: dict) -> str:
        p = Path(args["path"])
        if not p.exists():
            raise ToolError(f"File not found: {p}", "file_not_found")
        if p.is_dir():
            raise ToolError(f"Path is a directory: {p}", "invalid_path")

        try:
            lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            lines = p.read_text(encoding="gbk", errors="replace").splitlines(keepends=True)

        offset = args.get("offset", 0)
        limit = args.get("limit", len(lines))
        selected = lines[offset:offset + limit]
        return "".join(f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected))

    def _write(args: dict) -> str:
        p = Path(args["path"])
        if p.is_dir():
            raise ToolError(f"Path is a directory: {p}", "invalid_path")
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

    registry.register(Tool("read", "Read file with line numbers (file path, not directory)", {"path": "string", "offset": "number?", "limit": "number?"}, _read))
    registry.register(Tool("write", "Write content to file", {"path": "string", "content": "string"}, _write))
    registry.register(Tool("append", "Append content to file (creates if not exists)", {"path": "string", "content": "string"}, _append))
    registry.register(Tool("edit", "Replace old with new in file (old must be unique unless all=true)", {"path": "string", "old": "string", "new": "string", "all": "boolean?"}, _edit))

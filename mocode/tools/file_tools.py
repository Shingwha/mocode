"""文件操作工具"""

from .base import Tool, ToolRegistry


def _read(args: dict) -> str:
    """读取文件"""
    path = args["path"]
    lines = open(path, encoding="utf-8", errors="replace").readlines()
    offset = args.get("offset", 0)
    limit = args.get("limit", len(lines))
    selected = lines[offset : offset + limit]
    return "".join(
        f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected)
    )


def _write(args: dict) -> str:
    """写入文件"""
    with open(args["path"], "w", encoding="utf-8") as f:
        f.write(args["content"])
    return "ok"


def _edit(args: dict) -> str:
    """编辑文件（替换内容）"""
    path = args["path"]
    text = open(path, encoding="utf-8").read()
    old, new = args["old"], args["new"]

    if old not in text:
        return "error: old_string not found"

    count = text.count(old)
    if not args.get("all") and count > 1:
        return f"error: old_string appears {count} times, must be unique (use all=true)"

    replacement = (
        text.replace(old, new) if args.get("all") else text.replace(old, new, 1)
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(replacement)

    return "ok"


def register_file_tools():
    """注册文件操作工具"""
    ToolRegistry.register(
        Tool(
            "read",
            "Read file with line numbers (file path, not directory)",
            {"path": "string", "offset": "number?", "limit": "number?"},
            _read,
        )
    )
    ToolRegistry.register(
        Tool(
            "write",
            "Write content to file",
            {"path": "string", "content": "string"},
            _write,
        )
    )
    ToolRegistry.register(
        Tool(
            "edit",
            "Replace old with new in file (old must be unique unless all=true)",
            {"path": "string", "old": "string", "new": "string", "all": "boolean?"},
            _edit,
        )
    )

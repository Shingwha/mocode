"""搜索工具"""

import glob as globlib
import os
import re

from .base import Tool, ToolRegistry


def _glob(args: dict) -> str:
    """文件模式匹配"""
    pattern = (args.get("path", ".") + "/" + args["pat"]).replace("//", "/")
    files = globlib.glob(pattern, recursive=True)
    files = sorted(
        files,
        key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0,
        reverse=True,
    )
    return "\n".join(files) or "none"


def _grep(args: dict) -> str:
    """正则搜索文件内容"""
    pattern = re.compile(args["pat"])
    hits = []
    base_path = args.get("path", ".")

    for filepath in globlib.glob(base_path + "/**", recursive=True):
        try:
            if not os.path.isfile(filepath):
                continue
            for line_num, line in enumerate(open(filepath, encoding="utf-8", errors="replace"), 1):
                if pattern.search(line):
                    hits.append(f"{filepath}:{line_num}:{line.rstrip()}")
        except Exception:
            pass

    return "\n".join(hits[:50]) or "none"


def register_search_tools():
    """注册搜索工具"""
    ToolRegistry.register(
        Tool(
            "glob",
            "Find files by pattern, sorted by mtime",
            {"pat": "string", "path": "string?"},
            _glob,
        )
    )
    ToolRegistry.register(
        Tool(
            "grep",
            "Search files for regex pattern",
            {"pat": "string", "path": "string?"},
            _grep,
        )
    )

"""搜索工具"""

import glob as globlib
import os
import re
from pathlib import Path

from .base import Tool, ToolRegistry

# 默认忽略的目录
IGNORE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "target", "env",
})

# 文本文件扩展名白名单
TEXT_EXTENSIONS = frozenset({
    # 编程语言
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".kt", ".swift", ".rb", ".php", ".cs", ".scala", ".lua", ".r", ".m", ".mm",
    # 脚本和配置
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".fish",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    # 文档
    ".txt", ".md", ".rst", ".adoc", ".tex", ".org",
    # Web
    ".html", ".css", ".scss", ".less", ".sass", ".vue", ".svelte",
    # 数据
    ".sql", ".xml", ".svg", ".csv", ".tsv",
    # 其他
    ".dockerfile", ".makefile", ".cmake", ".gradle",
    ".gitignore", ".gitattributes", ".editorconfig",
})


def _is_text_file(path: str) -> bool:
    """判断是否为文本文件"""
    suffix = Path(path).suffix.lower()
    # 有扩展名且在白名单中，或无扩展名（如 Makefile）
    return suffix in TEXT_EXTENSIONS or not suffix


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
    """正则搜索文件内容（优化版）

    优化点：
    - 排除常见无关目录（.git, node_modules 等）
    - 仅搜索文本文件（基于扩展名白名单）
    - 可配置最大结果数
    """
    pattern = re.compile(args["pat"])
    base_path = args.get("path", ".")
    max_results = args.get("limit", 100)

    hits = []

    for root, dirs, files in os.walk(base_path):
        # 排除无关目录
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for filename in files:
            filepath = os.path.join(root, filename)

            # 仅处理文本文件
            if not _is_text_file(filepath):
                continue

            try:
                with open(filepath, encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, 1):
                        if pattern.search(line):
                            hits.append(f"{filepath}:{line_num}:{line.rstrip()}")
                            if len(hits) >= max_results:
                                return "\n".join(hits)
            except Exception:
                pass

    return "\n".join(hits) or "none"


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
            "Search files for regex pattern (excludes .git, node_modules, etc.)",
            {"pat": "string", "path": "string?", "limit": "number?"},
            _grep,
        )
    )

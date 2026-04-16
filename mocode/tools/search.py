"""搜索工具 — 工厂函数注册模式"""

import glob as globlib
import os
import re
from pathlib import Path

from ..tool import Tool, ToolRegistry

IGNORE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "target", "env",
})

TEXT_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".kt", ".swift", ".rb", ".php", ".cs", ".scala", ".lua", ".r", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".fish",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".txt", ".md", ".rst", ".adoc", ".tex", ".org",
    ".html", ".css", ".scss", ".less", ".sass", ".vue", ".svelte",
    ".sql", ".xml", ".svg", ".csv", ".tsv",
    ".dockerfile", ".makefile", ".cmake", ".gradle",
    ".gitignore", ".gitattributes", ".editorconfig",
})


def _is_text_file(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in TEXT_EXTENSIONS or not suffix


def register_search_tools(registry: ToolRegistry) -> None:
    """注册搜索工具"""

    def _glob(args: dict) -> str:
        pattern = (args.get("path", ".") + "/" + args["pat"]).replace("//", "/")
        files = globlib.glob(pattern, recursive=True)
        files = sorted(
            files,
            key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0,
            reverse=True,
        )
        return "\n".join(files) or "none"

    def _grep(args: dict) -> str:
        pattern = re.compile(args["pat"])
        base_path = args.get("path", ".")
        max_results = args.get("limit", 100)

        hits = []
        for root, dirs, files in os.walk(base_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for filename in files:
                filepath = os.path.join(root, filename)
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

    registry.register(Tool("glob", "Find files by pattern, sorted by mtime", {"pat": "string", "path": "string?"}, _glob))
    registry.register(Tool("grep", "Search files for regex pattern (excludes .git, node_modules, etc.)", {"pat": "string", "path": "string?", "limit": "number?"}, _grep))

"""Search tools — GlobTool, GrepTool."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..core.tool import Tool


IGNORE_DIRS = frozenset({
    # VCS
    ".git", ".svn", ".hg",
    # Python
    "__pycache__", ".venv", "venv", "env",
    ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    # JS/TS
    "node_modules", ".next", ".nuxt",
    # JVM
    ".gradle",
    # Rust
    "target",
    # Build output
    "dist", "build",
    # IDE
    ".idea", ".vscode",
    # Other
    ".cache", "coverage", ".terraform",
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


def _glob(args: dict) -> str:
    base = Path(args.get("path", ".")).resolve()
    if not base.is_dir():
        return f"error: path not found: {base}"
    pat = args["pat"]
    files = sorted(
        (str(p) for p in base.glob(pat)
         if p.is_file()
         and not any(part in IGNORE_DIRS for part in p.relative_to(base).parts)),
        key=lambda f: os.path.getmtime(f),
        reverse=True,
    )
    return "\n".join(files) or "none"


def _grep(args: dict) -> str:
    pattern = re.compile(args["pat"])
    base_path = str(Path(args.get("path", ".")).resolve())
    max_results = args.get("limit") or 100

    if not Path(base_path).is_dir():
        return f"error: path not found: {base_path}"

    hits = []
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for filename in files:
            if not _is_text_file(filename):
                continue
            filepath = os.path.join(root, filename)
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


GlobTool = Tool(
    "glob",
    "Find files by pattern, sorted by mtime (excludes .git, node_modules, etc.)",
    {"pat": "string", "path": "string?"},
    _glob,
)

GrepTool = Tool(
    "grep",
    "Search files for regex pattern (excludes .git, node_modules, etc.)",
    {"pat": "string", "path": "string?", "limit": "number?"},
    _grep,
)

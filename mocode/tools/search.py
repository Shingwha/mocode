"""Search tools — GlobTool, GrepTool."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..core.tool import Tool, ToolError
from ._helpers import require_dir

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

TYPE_MAP = {
    "py": ".py", "js": ".js", "ts": ".ts", "tsx": ".tsx", "jsx": ".jsx",
    "go": ".go", "rs": ".rs", "java": ".java", "c": ".c", "cpp": ".cpp",
    "h": ".h", "rb": ".rb", "php": ".php", "cs": ".cs", "swift": ".swift",
    "kt": ".kt", "scala": ".scala", "lua": ".lua", "r": ".r",
    "html": ".html", "css": ".css", "vue": ".vue", "svelte": ".svelte",
    "json": ".json", "yaml": ".yaml", "yml": ".yml", "toml": ".toml",
    "md": ".md", "txt": ".txt", "sql": ".sql", "xml": ".xml", "sh": ".sh",
}

TEXT_EXTENSIONS = frozenset(set(TYPE_MAP.values()) | {
    ".hpp", ".kt", ".m", ".mm",
    ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".fish",
    ".ini", ".cfg", ".conf", ".env",
    ".rst", ".adoc", ".tex", ".org",
    ".scss", ".less", ".sass",
    ".svg", ".csv", ".tsv",
    ".dockerfile", ".makefile", ".cmake",
    ".gitignore", ".gitattributes", ".editorconfig",
})

_GLOB_MAX = 200


def _is_text_file(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in TEXT_EXTENSIONS or not suffix


def _get_type_filter(type_str: str) -> set[str] | None:
    if not type_str:
        return None
    extensions = set()
    for t in type_str.split(","):
        t = t.strip().lower()
        if t in TYPE_MAP:
            extensions.add(TYPE_MAP[t])
        else:
            ext = t if t.startswith(".") else f".{t}"
            extensions.add(ext)
    return extensions


def _walk_text_files(base_path: Path, type_filter: set[str] | None):
    """Yield text file paths under base_path, respecting IGNORE_DIRS and type filter."""
    for root, dirs, _files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for filename in _files:
            if not _is_text_file(filename):
                continue
            if type_filter and Path(filename).suffix.lower() not in type_filter:
                continue
            yield os.path.join(root, filename)


def _glob(args: dict) -> str:
    base = require_dir(Path(args.get("path", ".")).resolve())
    pat = args["pat"]
    files = sorted(
        (p for p in base.glob(pat)
         if p.is_file()
         and not any(part in IGNORE_DIRS for part in p.relative_to(base).parts)),
        key=lambda f: os.path.getmtime(f),
        reverse=True,
    )

    if not files:
        return f"No files matching '{pat}' in {base}"

    truncated = len(files) > _GLOB_MAX
    files = files[:_GLOB_MAX]

    # Show relative paths when base is cwd
    cwd = Path.cwd()
    if base == cwd:
        paths = [str(p.relative_to(base)) for p in files]
    else:
        paths = [str(p) for p in files]

    header = f"[Found {len(files)}{'+' if truncated else ''} files matching '{pat}']"
    result = header + "\n" + "\n".join(paths)

    if truncated:
        result += f"\n... and more files not shown (showing first {_GLOB_MAX})"
    return result


def _grep(args: dict) -> str:
    pattern = re.compile(args["pat"])
    base_path = require_dir(Path(args.get("path", ".")).resolve())
    max_results = int(args.get("limit", 100)) or 100
    type_filter = _get_type_filter(args.get("type", ""))
    output_mode = args.get("output_mode", "content")
    context_lines = int(args.get("context", 0))

    if output_mode == "files":
        return _grep_files(pattern, base_path, type_filter, max_results)
    elif output_mode == "count":
        return _grep_count(pattern, base_path, type_filter, max_results)
    else:
        return _grep_content(pattern, base_path, type_filter, max_results, context_lines)


def _grep_files(pattern: re.Pattern, base_path: Path, type_filter: set[str] | None, max_results: int) -> str:
    found = []
    for filepath in _walk_text_files(base_path, type_filter):
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if pattern.search(line):
                        found.append(filepath)
                        break
        except Exception:
            pass
        if len(found) >= max_results:
            break

    if not found:
        return f"No files matching '{pattern.pattern}' in {base_path}"

    cwd = Path.cwd()
    paths = [str(Path(f).relative_to(cwd)) if Path(f).is_relative_to(cwd) else f for f in found]
    header = f"[Found {len(paths)} file(s)]"
    return header + "\n" + "\n".join(paths)


def _grep_count(pattern: re.Pattern, base_path: Path, type_filter: set[str] | None, max_results: int) -> str:
    results = []
    for filepath in _walk_text_files(base_path, type_filter):
        try:
            count = 0
            with open(filepath, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if pattern.search(line):
                        count += 1
            if count > 0:
                results.append(f"{filepath}:{count}")
        except Exception:
            pass
        if len(results) >= max_results:
            break

    if not results:
        return f"No matches for '{pattern.pattern}' in {base_path}"
    return "\n".join(results)


def _grep_content(pattern: re.Pattern, base_path: Path, type_filter: set[str] | None, max_results: int, context_lines: int) -> str:
    hits = []
    cwd = Path.cwd()
    for filepath in _walk_text_files(base_path, type_filter):
        try:
            file_lines = Path(filepath).read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        match_indices = [i for i, line in enumerate(file_lines) if pattern.search(line)]
        if not match_indices:
            continue

        display_path = str(Path(filepath).relative_to(cwd)) if Path(filepath).is_relative_to(cwd) else filepath

        if context_lines > 0:
            expanded = set()
            for idx in match_indices:
                for j in range(max(0, idx - context_lines), min(len(file_lines), idx + context_lines + 1)):
                    expanded.add(j)
            display_indices = sorted(expanded)
        else:
            display_indices = match_indices

        match_set = set(match_indices)
        for idx in display_indices:
            sep = ":" if idx in match_set else "-"
            hits.append(f"{display_path}{sep}{idx + 1}{sep}{file_lines[idx]}")
            if len(hits) >= max_results:
                header = f"[Showing {len(hits)} matches for '{pattern.pattern}']"
                return header + "\n" + "\n".join(hits)

    if not hits:
        return f"No matches for '{pattern.pattern}' in {base_path}"

    header = f"[Showing {len(hits)} match(es) for '{pattern.pattern}']"
    return header + "\n" + "\n".join(hits)


def GlobTool() -> Tool:
    return Tool(
        "glob",
        "Find files matching a glob pattern, sorted by modification time (newest first). "
        "Automatically excludes .git, node_modules, __pycache__, and other common non-project directories.",
        {
            "pat": {"type": "string", "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts')"},
            "path": {"type": "string", "description": "Base directory to search in (defaults to current directory)", "default": "."},
        },
        _glob,
    )


def GrepTool() -> Tool:
    return Tool(
        "grep",
        "Search file contents for a regex pattern across a directory tree. "
        "Automatically excludes .git, node_modules, __pycache__, and other non-project directories. "
        "Only searches text files (skips binary files by extension). "
        "Use 'type' to filter by file extension (e.g. 'py' for Python files). "
        "Use 'context' to show surrounding lines. Use 'output_mode' to control output format.",
        {
            "pat": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory to search in (defaults to current directory)", "default": "."},
            "type": {"type": "string", "description": "File extension filter, e.g. 'py', 'js', 'go' (comma-separated for multiple)", "default": ""},
            "output_mode": {"type": "string", "description": "Output format: 'content' shows lines, 'files' shows file paths, 'count' shows match counts", "enum": ["content", "files", "count"], "default": "content"},
            "context": {"type": "integer", "description": "Number of context lines before and after each match", "default": 0},
            "limit": {"type": "integer", "description": "Max results (default 100)", "default": 100},
        },
        _grep,
    )

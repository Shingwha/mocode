"""Search tools — GlobTool, GrepTool."""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterable
from pathlib import Path

from ..core.tool import Tool
from ..core.virtualfs import VirtualFS, _strip_prefix
from ._helpers import require_dir
from .utils import (
    expand_context_indices,
    format_grep_content,
    format_grep_count,
    format_grep_files,
)

IGNORE_DIRS = frozenset(
    {
        # VCS
        ".git",
        ".svn",
        ".hg",
        # Python
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        # JS/TS
        "node_modules",
        ".next",
        ".nuxt",
        # JVM
        ".gradle",
        # Rust
        "target",
        # Build output
        "dist",
        "build",
        # IDE
        ".idea",
        ".vscode",
        # Other
        ".cache",
        "coverage",
        ".terraform",
    }
)

TYPE_MAP = {
    "py": ".py",
    "js": ".js",
    "ts": ".ts",
    "tsx": ".tsx",
    "jsx": ".jsx",
    "go": ".go",
    "rs": ".rs",
    "java": ".java",
    "c": ".c",
    "cpp": ".cpp",
    "h": ".h",
    "rb": ".rb",
    "php": ".php",
    "cs": ".cs",
    "swift": ".swift",
    "kt": ".kt",
    "scala": ".scala",
    "lua": ".lua",
    "r": ".r",
    "html": ".html",
    "css": ".css",
    "vue": ".vue",
    "svelte": ".svelte",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
    "toml": ".toml",
    "md": ".md",
    "txt": ".txt",
    "sql": ".sql",
    "xml": ".xml",
    "sh": ".sh",
}

TEXT_EXTENSIONS = frozenset(
    set(TYPE_MAP.values())
    | {
        ".hpp",
        ".m",
        ".mm",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".cmd",
        ".fish",
        ".ini",
        ".cfg",
        ".conf",
        ".env",
        ".rst",
        ".adoc",
        ".tex",
        ".org",
        ".scss",
        ".less",
        ".sass",
        ".svg",
        ".csv",
        ".tsv",
        ".dockerfile",
        ".makefile",
        ".cmake",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
    }
)

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


# ── vfs helpers ────────────────────────────────────────────


def _is_vfs_path(path: str) -> bool:
    return path.startswith("vfs://")


def _match_vfs_type(path: str, type_filter: set[str] | None) -> bool:
    if not type_filter:
        return True
    suffix = ("." + path.rsplit(".", 1)[-1]) if "." in path else ""
    return suffix in type_filter


# ── glob ─────────────────────────────────────────────────────


def _glob(args: dict, vfs: VirtualFS | None = None) -> str:
    pattern = args["pattern"]
    base_path = args.get("path", ".")

    # VFS-only search
    if _is_vfs_path(pattern) or _is_vfs_path(base_path):
        if not vfs:
            return "No virtual file system available"
        vfs_path = base_path if _is_vfs_path(base_path) else None
        clean_pattern = _strip_prefix(pattern)
        vfs_files = sorted(
            p
            for p in vfs.iter_files(vfs_path)
            if fnmatch.fnmatch(_strip_prefix(p), clean_pattern)
        )
        if not vfs_files:
            return f"No virtual files matching '{pattern}'"
        return (
            f"[Found {len(vfs_files)} virtual file(s) matching '{pattern}']\n"
            + "\n".join(vfs_files)
        )

    # Real filesystem search
    base = require_dir(Path(base_path).resolve())
    files = sorted(
        (
            p
            for p in base.glob(pattern)
            if p.is_file()
            and not any(part in IGNORE_DIRS for part in p.relative_to(base).parts)
        ),
        key=lambda f: os.path.getmtime(f),
        reverse=True,
    )

    if not files:
        return f"No files matching '{pattern}' in {base}"

    truncated = len(files) > _GLOB_MAX
    files = files[:_GLOB_MAX]

    cwd = Path.cwd()
    paths = [str(p.relative_to(base)) if base == cwd else str(p) for p in files]

    header = f"[Found {len(files)}{'+' if truncated else ''} file(s) matching '{pattern}']"
    result = header + "\n" + "\n".join(paths)

    if truncated:
        result += f"\n... and more files not shown (showing first {_GLOB_MAX})"
    return result


# ── grep ─────────────────────────────────────────────────────


def _search_files(
    pattern: re.Pattern,
    file_iter: Iterable[tuple[str, list[str]]],
    output_mode: str,
    context_lines: int,
    max_results: int,
    not_found_msg: str,
) -> str:
    """Core grep logic — operates on ``(display_path, lines)`` pairs.

    This is the single implementation for all grep variants (VFS, real-fs,
    single-file).  Each variant only needs to provide an iterator that yields
    ``(display_path, file_lines)`` tuples.
    """
    if output_mode == "files":
        found: list[str] = []
        for display_path, file_lines in file_iter:
            if any(pattern.search(line) for line in file_lines):
                found.append(display_path)
                if len(found) >= max_results:
                    break
        return format_grep_files(found, pattern.pattern)

    if output_mode == "count":
        entries: list[str] = []
        for display_path, file_lines in file_iter:
            count = sum(1 for line in file_lines if pattern.search(line))
            if count > 0:
                entries.append(f"{display_path}:{count}")
                if len(entries) >= max_results:
                    break
        return format_grep_count(entries, pattern.pattern)

    # content mode
    hits: list[str] = []
    for display_path, file_lines in file_iter:
        match_indices = [
            i for i, line in enumerate(file_lines) if pattern.search(line)
        ]
        if not match_indices:
            continue
        display_indices = expand_context_indices(
            match_indices, len(file_lines), context_lines
        )
        result = format_grep_content(
            file_lines, match_indices, display_indices,
            display_path, pattern.pattern, max_results, hits,
        )
        if result:
            return result
    if not hits:
        return not_found_msg
    return (
        f"[Showing {len(hits)} match(es) for '{pattern.pattern}']\n"
        + "\n".join(hits)
    )


def _grep_single_file(
    pattern: re.Pattern,
    filepath: Path,
    output_mode: str,
    context_lines: int,
    max_results: int,
) -> str:
    """Search a single file and return formatted results."""
    try:
        lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return f"Cannot read file: {filepath}"

    return _search_files(
        pattern,
        [(str(filepath), lines)],
        output_mode, context_lines, max_results,
        f"No matches for '{pattern.pattern}' in {filepath}",
    )


def _grep(args: dict, vfs: VirtualFS | None = None) -> str:
    raw_ic = args.get("ignore_case", False)
    ignore_case = (
        str(raw_ic).lower() in ("true", "1", "yes")
        if isinstance(raw_ic, str)
        else bool(raw_ic)
    )
    flags = re.IGNORECASE if ignore_case else 0
    pattern = re.compile(args["pattern"], flags)
    base_path = args.get("path", ".")
    max_results = int(args.get("limit", 100)) or 100
    type_filter = _get_type_filter(args.get("type", ""))
    output_mode = args.get("output_mode", "content")
    context_lines = int(args.get("context", 0))

    # VFS-only search
    if _is_vfs_path(base_path):
        if not vfs:
            return "No virtual file system available"
        return _grep_vfs(
            pattern, vfs, base_path,
            type_filter, output_mode, max_results, context_lines,
        )

    # Single file search — when path points to a file, search only that file
    target = Path(base_path).resolve()
    if target.is_file():
        return _grep_single_file(
            pattern, target, output_mode, context_lines, max_results
        )

    # Real filesystem search (directory)
    real_path = require_dir(target)
    return _grep_real_fs(
        pattern, real_path,
        type_filter, output_mode, max_results, context_lines,
    )


def _grep_vfs(
    pattern: re.Pattern,
    vfs: VirtualFS,
    base_path: str,
    type_filter: set[str] | None,
    output_mode: str,
    max_results: int,
    context_lines: int,
) -> str:
    """Search VFS files using shared formatting utilities."""

    def _iter() -> Iterable[tuple[str, list[str]]]:
        for p in vfs.iter_files(base_path):
            if _match_vfs_type(p, type_filter):
                yield p, vfs.get(p).splitlines()

    return _search_files(
        pattern, _iter(),
        output_mode, context_lines, max_results,
        f"No matches for '{pattern.pattern}' in virtual files",
    )


def _grep_real_fs(
    pattern: re.Pattern,
    base_path: Path,
    type_filter: set[str] | None,
    output_mode: str,
    max_results: int,
    context_lines: int,
) -> str:
    """Search real filesystem using shared formatting utilities."""
    cwd = Path.cwd()

    def _iter() -> Iterable[tuple[str, list[str]]]:
        for filepath in _walk_text_files(base_path, type_filter):
            try:
                lines = Path(filepath).read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except Exception:
                continue
            dp = (
                str(Path(filepath).relative_to(cwd))
                if Path(filepath).is_relative_to(cwd)
                else filepath
            )
            yield dp, lines

    return _search_files(
        pattern, _iter(),
        output_mode, context_lines, max_results,
        f"No matches for '{pattern.pattern}' in {base_path}",
    )


# ── tool factories ───────────────────────────────────────────


def GlobTool(vfs: VirtualFS | None = None) -> Tool:
    return Tool(
        "glob",
        "Find files matching a glob pattern, sorted by modification time (newest first). "
        "Automatically excludes .git, node_modules, __pycache__, and other common non-project directories. "
        "Supports virtual files via vfs:// prefix. "
        "Real filesystem paths search only real files; the two are never mixed.",
        {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts', 'vfs://**/*.md')",
            },
            "path": {
                "type": "string",
                "description": "Base directory to search in (defaults to current directory). Use 'vfs://' to search virtual files only.",
                "default": ".",
            },
        },
        lambda args: _glob(args, vfs),
    )


def GrepTool(vfs: VirtualFS | None = None) -> Tool:
    return Tool(
        "grep",
        "Search file contents for a regex pattern across a directory tree. "
        "Automatically excludes .git, node_modules, __pycache__, and other common non-project directories. "
        "Only searches text files (skips binary files by extension). "
        "Use 'type' to filter by file extension (e.g. 'py' for Python files). "
        "Use 'context' to show surrounding lines. Use 'output_mode' to control output format. "
        "Use 'ignore_case' for case-insensitive matching. "
        "Supports virtual files — use path='vfs://' to search virtual files only. "
        "Real filesystem paths search only real files; the two are never mixed.",
        {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {
                "type": "string",
                "description": "Directory to search in (defaults to current directory). Also accepts a single file path. Use 'vfs://' to search virtual files only.",
                "default": ".",
            },
            "type": {
                "type": "string",
                "description": "File extension filter, e.g. 'py', 'js', 'go' (comma-separated for multiple)",
                "default": "",
            },
            "output_mode": {
                "type": "string",
                "description": "Output format: 'content' shows lines, 'files' shows file paths, 'count' shows match counts",
                "enum": ["content", "files", "count"],
                "default": "content",
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines before and after each match",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 100)",
                "default": 100,
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive matching (default false)",
                "default": False,
            },
        },
        lambda args: _grep(args, vfs),
    )

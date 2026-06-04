"""Virtual file system — in-memory files addressable via vfs:// URIs.

Skills can embed auxiliary content (examples, templates, references) as
virtual files.  Read / Glob / Grep tools transparently access them.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


_VFS_PREFIX = "vfs://"


def _normalize(path: str) -> str:
    """Ensure *path* starts with ``vfs://``."""
    return path if path.startswith(_VFS_PREFIX) else _VFS_PREFIX + path


def _strip_prefix(path: str) -> str:
    """Remove the ``vfs://`` prefix if present."""
    return path.removeprefix(_VFS_PREFIX)


class VirtualFS:
    """Dict-backed virtual filesystem for embedded skill content."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    # ── mutation ──────────────────────────────────────────────

    def add(self, path: str, content: str) -> None:
        """Register a virtual file.  Auto-prepends ``vfs://`` if missing."""
        self._files[_normalize(path)] = content

    def remove(self, path: str) -> bool:
        """Remove a virtual file.  Returns True if it existed."""
        return self._files.pop(_normalize(path), None) is not None

    # ── query ─────────────────────────────────────────────────

    def get(self, path: str) -> str | None:
        return self._files.get(_normalize(path))

    def exists(self, path: str) -> bool:
        return _normalize(path) in self._files

    def list(self) -> list[str]:
        """Return all virtual file paths (with ``vfs://`` prefix)."""
        return list(self._files.keys())

    def items(self) -> list[tuple[str, str]]:
        """Return all ``(path, content)`` pairs."""
        return list(self._files.items())

    # ── search ────────────────────────────────────────────────

    def _iter_files(self, path: str | None = None):
        """Yield vfs paths, optionally filtered to those under *path*."""
        if path:
            prefix = _normalize(path)
            if prefix != _VFS_PREFIX:
                if not prefix.endswith("/"):
                    prefix += "/"
                return (p for p in self._files if p.startswith(prefix))
        return iter(self._files)

    def glob(self, pattern: str, *, path: str | None = None) -> list[str]:
        """Match virtual file paths against a glob pattern.

        *pattern* is matched against the path **after** the ``vfs://`` prefix.
        *path* restricts the search to files under that VFS subdirectory.
        Returns matching paths **with** the ``vfs://`` prefix.
        """
        clean_pattern = _strip_prefix(pattern)
        return sorted(
            p for p in self._iter_files(path)
            if fnmatch.fnmatch(_strip_prefix(p), clean_pattern)
        )

    def grep(
        self,
        pattern: str | re.Pattern,
        *,
        type_filter: set[str] | None = None,
        output_mode: str = "content",
        max_results: int = 100,
        context_lines: int = 0,
        ignore_case: bool = False,
        path: str | None = None,
    ) -> str:
        """Search virtual file contents.

        Returns a formatted string matching the tool output conventions.
        *output_mode* is one of ``"content"``, ``"files"``, ``"count"``.
        *path* restricts the search to files under that VFS subdirectory.
        """
        if isinstance(pattern, str):
            flags = re.IGNORECASE if ignore_case else 0
            pattern = re.compile(pattern, flags)

        if output_mode == "files":
            return self._grep_files(pattern, type_filter, max_results, path)
        if output_mode == "count":
            return self._grep_count(pattern, type_filter, max_results, path)
        return self._grep_content(pattern, type_filter, max_results, context_lines, path)

    # ── grep internals ────────────────────────────────────────

    def _match_filter(self, path: str, type_filter: set[str] | None) -> bool:
        if not type_filter:
            return True
        suffix = ("." + path.rsplit(".", 1)[-1]) if "." in path else ""
        return suffix in type_filter

    def _grep_files(
        self,
        pattern: re.Pattern,
        type_filter: set[str] | None,
        max_results: int,
        path: str | None = None,
    ) -> str:
        found: list[str] = []
        for p in self._iter_files(path):
            if not self._match_filter(p, type_filter):
                continue
            for line in self._files[p].splitlines():
                if pattern.search(line):
                    found.append(p)
                    break
            if len(found) >= max_results:
                break
        if not found:
            return f"No virtual files matching '{pattern.pattern}'"
        return f"[Found {len(found)} virtual file(s)]\n" + "\n".join(found)

    def _grep_count(
        self,
        pattern: re.Pattern,
        type_filter: set[str] | None,
        max_results: int,
        path: str | None = None,
    ) -> str:
        results: list[str] = []
        for p in self._iter_files(path):
            if not self._match_filter(p, type_filter):
                continue
            count = sum(1 for line in self._files[p].splitlines() if pattern.search(line))
            if count > 0:
                results.append(f"{p}:{count}")
            if len(results) >= max_results:
                break
        if not results:
            return f"No matches for '{pattern.pattern}' in virtual files"
        return "\n".join(results)

    def _grep_content(
        self,
        pattern: re.Pattern,
        type_filter: set[str] | None,
        max_results: int,
        context_lines: int,
        path: str | None = None,
    ) -> str:
        hits: list[str] = []
        for p in self._iter_files(path):
            if not self._match_filter(p, type_filter):
                continue
            file_lines = self._files[p].splitlines()
            match_indices = [i for i, line in enumerate(file_lines) if pattern.search(line)]
            if not match_indices:
                continue
            if context_lines > 0:
                expanded: set[int] = set()
                for idx in match_indices:
                    for j in range(
                        max(0, idx - context_lines),
                        min(len(file_lines), idx + context_lines + 1),
                    ):
                        expanded.add(j)
                display_indices = sorted(expanded)
            else:
                display_indices = match_indices
            match_set = set(match_indices)
            for idx in display_indices:
                sep = ":" if idx in match_set else "-"
                hits.append(f"{p}{sep}{idx + 1}{sep}{file_lines[idx]}")
                if len(hits) >= max_results:
                    return (
                        f"[Showing {len(hits)} matches for '{pattern.pattern}']\n"
                        + "\n".join(hits)
                    )
        if not hits:
            return f"No matches for '{pattern.pattern}' in virtual files"
        return (
            f"[Showing {len(hits)} match(es) for '{pattern.pattern}']\n"
            + "\n".join(hits)
        )

    # ── bulk mount ────────────────────────────────────────────

    def mount_directory(
        self,
        skill_dir: Path,
        namespace: str,
        *,
        skip: set[str] | None = None,
    ) -> dict[str, str]:
        """Discover and register all text files under *skill_dir*.

        Files are stored as ``vfs://{namespace}/{relative_path}``.
        Skips: SKILL.md, __init__.py, __pycache__/, .pyc files,
        and any filenames in *skip*.

        Returns the dict of ``{vfs_path: content}`` that were mounted.
        """
        _default_skip = {"SKILL.md", "__init__.py"}
        skip = (skip or set()) | _default_skip

        mounted: dict[str, str] = {}
        if not skill_dir.is_dir():
            return mounted

        for f in sorted(skill_dir.rglob("*")):
            if not f.is_file():
                continue
            if f.name in skip or f.suffix == ".pyc":
                continue
            if "__pycache__" in f.parts:
                continue
            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = f.relative_to(skill_dir).as_posix()
            vfs_path = f"{_VFS_PREFIX}{namespace}/{rel}"
            self._files[vfs_path] = content
            mounted[vfs_path] = content

        return mounted

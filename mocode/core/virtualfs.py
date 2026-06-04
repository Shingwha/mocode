"""Virtual file system — in-memory files addressable via vfs:// URIs.

Skills can embed auxiliary content (examples, templates, references) as
virtual files.  Read / Glob / Grep tools transparently access them.
"""

from __future__ import annotations

import fnmatch
import re


_VFS_PREFIX = "vfs://"


class VirtualFS:
    """Simple dict-backed virtual filesystem for embedded skill content."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    # ── mutation ──────────────────────────────────────────────

    def add(self, path: str, content: str) -> None:
        """Register a virtual file. Auto-prepends ``vfs://`` if missing."""
        if not path.startswith(_VFS_PREFIX):
            path = _VFS_PREFIX + path
        self._files[path] = content

    # ── query ─────────────────────────────────────────────────

    def get(self, path: str) -> str | None:
        if not path.startswith(_VFS_PREFIX):
            path = _VFS_PREFIX + path
        return self._files.get(path)

    def exists(self, path: str) -> bool:
        if not path.startswith(_VFS_PREFIX):
            path = _VFS_PREFIX + path
        return path in self._files

    def list(self) -> list[str]:
        """Return all virtual file paths (with ``vfs://`` prefix)."""
        return list(self._files.keys())

    def glob(self, pattern: str) -> list[str]:
        """Match virtual file paths against a glob pattern.

        *pattern* is matched against the path **after** the ``vfs://`` prefix.
        Returns matching paths **with** the ``vfs://`` prefix.
        """
        clean_pattern = pattern.lstrip("vfs://")
        results = []
        for path in self._files:
            clean_path = path.lstrip("vfs://")
            if fnmatch.fnmatch(clean_path, clean_pattern):
                results.append(path)
        return sorted(results)

    def grep(
        self,
        pattern: str | re.Pattern,
        type_filter: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """Search virtual file contents with regex.

        Returns list of ``(path, matching_line)`` tuples.
        """
        if isinstance(pattern, str):
            pattern = re.compile(pattern)

        results: list[tuple[str, str]] = []
        for path, content in self._files.items():
            if type_filter:
                suffix = ("." + path.rsplit(".", 1)[-1]) if "." in path else ""
                if suffix not in type_filter:
                    continue
            for line in content.splitlines():
                if pattern.search(line):
                    results.append((path, line))
        return results


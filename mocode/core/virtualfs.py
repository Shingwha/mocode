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

    def glob(self, pattern: str) -> list[str]:
        """Return virtual paths matching *pattern* (fnmatch on logical path)."""
        if not pattern.startswith(_VFS_PREFIX):
            # Pattern without prefix — match against the part after vfs://
            return sorted(
                p for p in self._files if fnmatch.fnmatch(p, _VFS_PREFIX + pattern)
            )
        return sorted(p for p in self._files if fnmatch.fnmatch(p, pattern))

    def grep(self, pattern: str) -> list[tuple[str, int, str]]:
        """Search virtual file contents. Returns ``[(path, line_no, line)]``."""
        regex = re.compile(pattern)
        hits: list[tuple[str, int, str]] = []
        for path, content in sorted(self._files.items()):
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    hits.append((path, i, line))
        return hits

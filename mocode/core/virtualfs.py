"""Virtual file system — in-memory files addressable via vfs:// URIs.

Skills can embed auxiliary content (examples, templates, references) as
virtual files.  Read / Glob / Grep tools transparently access them.
"""

from __future__ import annotations


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


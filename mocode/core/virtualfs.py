"""Virtual file system — in-memory files addressable via vfs:// URIs.

Skills can embed auxiliary content (examples, templates, references) as
virtual files.  Read / Glob / Grep tools transparently access them.
"""

from __future__ import annotations

import collections.abc


_VFS_PREFIX = "vfs://"


def _normalize(path: str) -> str:
    """Ensure *path* starts with ``vfs://``."""
    return path if path.startswith(_VFS_PREFIX) else _VFS_PREFIX + path


def _strip_prefix(path: str) -> str:
    """Remove the ``vfs://`` prefix if present."""
    return path.removeprefix(_VFS_PREFIX)


class VirtualFS(collections.abc.Mapping):
    """Dict-backed virtual filesystem for embedded skill content."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    # ── Mapping protocol (read-only) ─────────────────────────

    def __getitem__(self, path: str) -> str:
        return self._files[_normalize(path)]

    def __len__(self) -> int:
        return len(self._files)

    def __iter__(self):
        return iter(self._files)

    # ── Mutation ─────────────────────────────────────────────

    def add(self, path: str, content: str) -> None:
        """Register a virtual file.  Auto-prepends ``vfs://`` if missing."""
        self._files[_normalize(path)] = content

    def remove(self, path: str) -> bool:
        """Remove a virtual file.  Returns True if it existed."""
        return self._files.pop(_normalize(path), None) is not None

    # ── Convenience ──────────────────────────────────────────

    def exists(self, path: str) -> bool:
        return _normalize(path) in self._files

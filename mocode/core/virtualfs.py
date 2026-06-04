"""Virtual file system — in-memory files addressable via vfs:// URIs.

Skills can embed auxiliary content (examples, templates, references) as
virtual files.  Read / Glob / Grep tools transparently access them.
"""

from __future__ import annotations


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

    # ── iteration ─────────────────────────────────────────────

    def iter_files(self, path: str | None = None):
        """Yield vfs paths, optionally filtered to those under *path*.

        Used by search tools to iterate VFS files for glob/grep operations.
        """
        if path:
            prefix = _normalize(path)
            if prefix != _VFS_PREFIX:
                if not prefix.endswith("/"):
                    prefix += "/"
                return (p for p in self._files if p.startswith(prefix))
        return iter(self._files)

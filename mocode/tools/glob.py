"""Glob tool — find files by pattern."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.tool import Tool
from ..core.virtualfs import _VFS_PREFIX, _normalize, _strip_prefix
from .utils import IGNORE_DIRS, _GLOB_MAX, _is_vfs_path, require_dir

if TYPE_CHECKING:
    from ..core.virtualfs import VirtualFS


_GLOB_PARAMS = {
    "pattern": {
        "type": "string",
        "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts', 'vfs://**/*.md')",
    },
    "path": {
        "type": "string",
        "description": "Base directory to search in (defaults to current directory). Use 'vfs://' to search virtual files only.",
        "default": ".",
    },
}

_GLOB_DESC = (
    "Find files matching a glob pattern, sorted by modification time (newest first). "
    "Automatically excludes .git, node_modules, __pycache__, and other common non-project directories. "
    "Supports virtual files via vfs:// prefix. "
    "Real filesystem paths search only real files; the two are never mixed."
)


class GlobTool(Tool):
    """Find files matching a glob pattern."""

    def __init__(self, vfs: VirtualFS | None = None) -> None:
        self._vfs = vfs
        super().__init__(name="glob", description=_GLOB_DESC, params=_GLOB_PARAMS, func=self._execute)

    def _execute(self, args: dict) -> str:
        pattern = args["pattern"]
        base_path = args.get("path", ".")

        # VFS-only search
        if _is_vfs_path(pattern) or _is_vfs_path(base_path):
            if not self._vfs:
                return "No virtual file system available"
            clean_pattern = _strip_prefix(pattern)
            prefix = _normalize(base_path) if _is_vfs_path(base_path) else _VFS_PREFIX
            if prefix != _VFS_PREFIX:
                if not prefix.endswith("/"):
                    prefix += "/"
                keys = [p for p in self._vfs if p.startswith(prefix)]
            else:
                keys = list(self._vfs)
            vfs_files = sorted(
                p for p in keys
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

"""Search tools — GlobTool, GrepTool.

Backward-compatible re-export layer. The implementations have been split into:
- ``tools/glob.py`` — GlobTool
- ``tools/grep.py`` — GrepTool
"""

from __future__ import annotations

# Re-export for backward compatibility
from .glob import GlobTool, _glob  # noqa: F401
from .grep import GrepTool, _grep, _grep_real_fs, _grep_single_file, _grep_vfs, _search_files  # noqa: F401

__all__ = [
    "GlobTool",
    "GrepTool",
    "_glob",
    "_grep",
    "_grep_vfs",
    "_grep_real_fs",
    "_grep_single_file",
    "_search_files",
]

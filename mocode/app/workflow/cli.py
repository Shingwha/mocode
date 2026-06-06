"""Shared workflow utilities used by both the REPL ``/workflow`` command and
``app.py`` / ``__init__.py`` re-exports."""

from __future__ import annotations

from pathlib import Path

from .registry import WorkflowRegistry


def make_registry() -> WorkflowRegistry:
    """Build a WorkflowRegistry from config dirs."""
    dirs: list[Path] = []
    # Global workflows dir
    global_dir = Path.home() / ".mocode" / "workflows"
    dirs.append(global_dir)
    # Project-local workflows dir
    local_dir = Path.cwd() / ".mocode" / "workflows"
    if local_dir != global_dir:
        dirs.append(local_dir)

    return WorkflowRegistry(dirs=dirs)

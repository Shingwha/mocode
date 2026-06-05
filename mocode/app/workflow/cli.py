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


def parse_kv_args(kv_args: list[str] | None) -> dict[str, str]:
    """Parse ``key=value`` pairs from positional args."""
    result: dict[str, str] = {}
    for p in kv_args or []:
        if "=" in p:
            k, v = p.split("=", 1)
            result[k.strip()] = v.strip()
    return result

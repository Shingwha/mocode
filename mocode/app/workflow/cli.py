"""Shared workflow utilities used by both the REPL ``/workflow`` command and
``app.py`` / ``__init__.py`` re-exports."""

from __future__ import annotations

from .registry import WorkflowRegistry


def make_registry() -> WorkflowRegistry:
    """Build a WorkflowRegistry from default dirs."""
    return WorkflowRegistry.from_default_dirs()

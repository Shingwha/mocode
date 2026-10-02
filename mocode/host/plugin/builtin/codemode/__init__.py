"""The codemode builtin plugin — run a Python script that calls other tools.

Importing this package has no side effects: ``PLUGIN`` and ``CodemodePlugin``
resolve lazily so that merely importing a submodule (e.g. ``runtime`` in a
test) does not pull the whole plugin in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .plugin import CodemodePlugin, PLUGIN

__all__ = ["PLUGIN", "CodemodePlugin"]


def __getattr__(name: str):
    if name in ("PLUGIN", "CodemodePlugin"):
        from . import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

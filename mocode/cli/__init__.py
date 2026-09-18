"""mocode.cli — the terminal front-end.

It exports terminal things only. The command contract, the plugin framework and
the runtime all live in :mod:`mocode.host` — this package is a consumer of them,
not a second place to import them from.

What a turn looks like is :mod:`mocode.cli.lines`; how it reaches the screen is
:class:`~mocode.cli.display.Display`.
"""

from __future__ import annotations

from .app import CLIApp
from .display import Display
from .hook import CLIDisplayHook
from .theme import Theme

__all__ = [
    "CLIApp",
    "CLIDisplayHook",
    "Display",
    "Theme",
]

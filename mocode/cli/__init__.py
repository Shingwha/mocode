"""mocode.cli — the terminal front-end.

It exports terminal things only. The command contract, the plugin framework and
the runtime all live in :mod:`mocode.host` — this package is a consumer of them,
not a second place to import them from.

What a turn looks like is :mod:`mocode.cli.lines`; how it reaches the screen is
:class:`~mocode.cli.display.Display`; how a conversation's event stream becomes
those lines is :class:`~mocode.cli.render.CLIRenderer`.
"""

from __future__ import annotations

from .app import CLIApp
from .display import Display
from .plugin import CLIPlugin
from .render import CLIRenderer
from .theme import Theme

__all__ = [
    "CLIApp",
    "CLIPlugin",
    "CLIRenderer",
    "Display",
    "Theme",
]

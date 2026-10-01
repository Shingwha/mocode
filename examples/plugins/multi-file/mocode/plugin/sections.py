"""One contribution per submodule — this one owns the prompt section.

A submodule is an ordinary Python module: relative imports inside the
package are the only form the loader guarantees (``from .commands import
x``, ``from . import sections``), because each plugin's package carries a
name only it owns.
"""

from __future__ import annotations

from datetime import date

from mocode.plugins import Section


def motd() -> str:
    """The message of the day — the one fact both submodules share."""
    return f"Message of the day ({date.today().isoformat()}): ship small, ship often."


def motd_section() -> Section:
    """A prompt section the model always sees."""

    def _render(_context: dict) -> str:
        return motd()

    return Section("motd", _render, priority=45)

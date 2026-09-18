"""Frontend — what a plugin or command may ask of whatever is showing the run.

The host never assumes a terminal. A plugin with something to say asks this
protocol; the terminal implements it today, and a web or GUI frontend implements
it tomorrow. Everything else travels as an event on the run's stream.

Keep this small. The moment a method here only makes sense in one frontend, it
belongs in that frontend, not in the contract every frontend has to satisfy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..core.tool import ToolRegistry


@runtime_checkable
class Frontend(Protocol):
    """A thing that can show a run.

    Structural: anything with these four methods qualifies, so a frontend does
    not have to inherit from anything.
    """

    def info(self, text: str) -> None:
        """Show a neutral message."""

    def warn(self, text: str) -> None:
        """Show something the user should probably look at."""

    def error(self, text: str) -> None:
        """Show a failure."""

    def conversation_changed(
        self, messages: list[dict[str, Any]], tools: "ToolRegistry | None" = None
    ) -> None:
        """The conversation was replaced — resumed, imported or cleared. Redraw it.

        ``tools`` is what the renderer needs to describe tool calls; pass the
        registry the run is using, or ``None`` to fall back to bare arguments.
        """


__all__ = ["Frontend"]

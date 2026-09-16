"""Plugin — the extension contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import HostContext


class Plugin:
    """Base class for every MoCode plugin.

    Subclass and override :meth:`build` to contribute tools, commands, hooks or
    prompt sections to the host. All contributions happen in a single pass.

    ``ctx.agent`` is ``None`` during ``build()`` (the agent does not exist yet).
    Anything that needs the assembled agent — a sub-agent tool, a compactor —
    should keep the context and read ``ctx.agent`` at call time instead of
    capturing it here.
    """

    name: str = ""
    description: str = ""

    def build(self, ctx: HostContext) -> None:
        """Contribute to the host. Default: contribute nothing."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"

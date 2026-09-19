"""Plugin — the extension contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import HostContext


class Plugin:
    """Base class for every MoCode plugin.

    Subclass and override :meth:`build` to contribute tools, commands, hooks or
    prompt sections to the host. All contributions happen in a single pass.

    Two rules make a plugin usable in a runtime that holds many conversations:

    * **An instance is stateless.** ``build(ctx)`` runs once per conversation
      and it is the only place to create state — a shell session, an index, a
      tool holding a cursor. State kept on ``self`` is shared by every
      conversation in the process, which is not what any of them asked for.
    * **``ctx.agent`` is ``None`` during ``build()``** (the agent does not exist
      yet). Anything that needs the assembled agent — a sub-agent tool, a
      compactor — keeps the context and reads ``ctx.agent`` at call time instead
      of capturing it here.

    A plugin that acquires a resource for a conversation (a subscription, a
    background task) releases it in :meth:`close`.
    """

    name: str = ""
    description: str = ""

    def build(self, ctx: HostContext) -> None:
        """Contribute to the host. Default: contribute nothing."""

    def close(self, ctx: HostContext) -> None:
        """This conversation is over — release whatever was built for it."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"

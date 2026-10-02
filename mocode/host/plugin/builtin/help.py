"""help plugin — the one command every frontend can answer the same way.

Listing the command registry needs nothing but a conversation to publish on,
so it works headless; what a terminal draws it with is that terminal's
business.
"""

from __future__ import annotations

from ...command import CONTINUE, Command, CommandContext, CommandResult
from ..base import Plugin
from ..context import BuildContext


async def _help(ctx: CommandContext) -> CommandResult:
    """List every registered command — including plugin-contributed ones."""
    commands = ctx.commands.all()
    if not commands:
        return CONTINUE

    width = max(len(c.name) for c in commands)
    lines = []
    for cmd in commands:
        readable = ", ".join(a for a in cmd.aliases if not a.startswith("/"))
        alias = f"  (also: {readable})" if readable else ""
        lines.append(f"  {cmd.name:<{width}}  {cmd.description}{alias}")
    await ctx.conversation.notify("Commands:\n" + "\n".join(lines))
    return CONTINUE


class HelpPlugin(Plugin):
    """The builtin behind ``/help`` — the registry answers for itself.

    It reads the conversation's command registry rather than a list of its own,
    so a plugin contributing a command shows up in the help without anyone
    remembering to add it — no second table to keep in sync.
    """

    name = "help"
    description = "List the commands this conversation offers"

    def build(self, ctx: BuildContext) -> None:
        """Register the one ``/help`` command."""
        ctx.commands.register(Command("/help", "Show available commands", handler=_help))


PLUGIN = HelpPlugin()

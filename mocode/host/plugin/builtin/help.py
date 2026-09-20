"""help plugin — the one command every frontend can answer the same way.

Listing the command registry needs nothing but a conversation to publish on,
so it works headless; what a terminal draws it with is that terminal's
business.
"""

from __future__ import annotations

from ...command import CONTINUE, Command, CommandContext, CommandResult
from ..base import Plugin
from ..context import HostContext


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
    name = "help"
    description = "List the commands this conversation offers"

    def build(self, ctx: HostContext) -> None:
        ctx.register(Command("/help", "Show available commands", handler=_help))


PLUGIN = HelpPlugin()

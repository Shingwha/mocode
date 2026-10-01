"""The /motd command — a submodule importing from a sibling submodule.

The ``from .sections import motd`` below is the point of this file: code
moving between submodules of the package goes through relative import, the
same as any Python package. The loader gave the package a name derived from
the plugin's, so two plugins can both have a ``commands.py`` and neither
ever sees the other's.
"""

from __future__ import annotations

from mocode.plugins import CONTINUE, Command, CommandContext, CommandResult

from .sections import motd


async def _motd(ctx: CommandContext) -> CommandResult:
    """Say the message of the day — in any frontend, as a notice."""
    await ctx.conversation.notify(motd())
    return CONTINUE


def motd_command() -> Command:
    return Command("/motd", "Show the message of the day", handler=_motd)

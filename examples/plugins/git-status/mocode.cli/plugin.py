"""The plugin's terminal contributions — the `mocode.cli/` namespace.

This module is read by the terminal and by nothing else: another frontend (a web
backend, an editor) simply does not look here. That is what makes it the right
place for chrome — a command that needs a picker, a keybinding, something only a
terminal can honour.

The two namespaces never import each other; when they need to cooperate they go
through the conversation, which is the only thing they share.
"""

from __future__ import annotations

from mocode.cli import CLIPlugin
from mocode.plugins import CONTINUE, Command, CommandContext, CommandResult


async def _status(ctx: CommandContext) -> CommandResult:
    """Show the working tree, using the tool the host namespace contributed."""
    tool = ctx.conversation.tools.get("git_status")
    if tool is None:
        await ctx.conversation.notify("The git_status tool is not installed.", level="warn")
        return CONTINUE

    await ctx.conversation.notify("Working tree:\n" + tool.run({}))
    return CONTINUE


class GitStatusCLI(CLIPlugin):
    name = "git-status.cli"
    description = "git-status in the terminal: /status"

    def build(self, cli) -> None:
        cli.commands.register(
            Command("/status", "Show git status (terminal)", handler=_status)
        )


plugin = GitStatusCLI()

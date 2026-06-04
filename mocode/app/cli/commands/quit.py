"""Quit command — exits the REPL."""

from . import CommandContext, CommandResult


class QuitCommand:
    name = "/quit"
    description = "Exit the application"
    aliases = ("/exit", "quit", "exit")

    async def run(self, ctx: CommandContext) -> CommandResult:
        return CommandResult.EXIT

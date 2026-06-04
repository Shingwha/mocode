"""Clear command — saves and clears the current conversation."""

from . import CommandContext, CommandResult


class ClearCommand:
    name = "/clear"
    description = "Clear the current conversation"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        ctx.app.replace_messages([])
        ctx.display.info("Session saved and cleared.")
        return CommandResult.CONTINUE

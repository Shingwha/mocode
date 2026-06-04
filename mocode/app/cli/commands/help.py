"""Help command — lists all registered commands."""

from . import CommandContext, CommandResult


class HelpCommand:
    name = "/help"
    description = "Show available commands"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        commands = ctx.app.commands.all()
        if not commands:
            ctx.display.info("No commands available.")
            return CommandResult.CONTINUE

        max_len = max(len(c.name) for c in commands)
        lines = []
        for cmd in commands:
            alias_str = ""
            if cmd.aliases:
                readable = ", ".join(a for a in cmd.aliases if not a.startswith("/"))
                if readable:
                    alias_str = f"  (also: {readable})"
            lines.append(f"  {cmd.name:<{max_len}}  {cmd.description}{alias_str}")

        ctx.display.info("Commands:\n" + "\n".join(lines))
        return CommandResult.CONTINUE

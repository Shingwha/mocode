"""Built-in commands - quit, clear, help"""

from .base import Command, CommandContext, CommandRegistry, command
from .result import CommandResult


@command("/exit", "/q", "/quit", description="Exit program")
class QuitCommand(Command):
    def execute(self, ctx: CommandContext) -> CommandResult:
        return CommandResult(should_exit=True)


@command("/clear", "/c", description="Clear conversation history (auto-save)")
class ClearCommand(Command):
    def execute(self, ctx: CommandContext) -> CommandResult:
        saved = ctx.client.clear_history_with_save()
        msg = f"Session saved: {saved.id}" if saved else ""
        return CommandResult(
            success=True,
            message="Conversation cleared",
            data={"saved_session": saved, "message": msg},
        )


@command("/", "/help", "/h", "/?", description="Select command")
class HelpCommand(Command):
    def execute(self, ctx: CommandContext) -> CommandResult:
        registry = CommandRegistry()
        commands = [
            {"name": c.name, "aliases": c.aliases, "description": c.description}
            for c in registry.all()
        ]

        if not ctx.args:
            return CommandResult(data={"commands": commands})

        return CommandResult(data={"commands": commands})

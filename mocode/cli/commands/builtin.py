"""Built-in commands"""

import os

from ..ui.prompt import select, MenuItem, is_cancelled
from ..ui.styles import DIM, RESET, MessagePreset, format_preset
from .base import Command, CommandContext, command


@command("/exit", "/q", "/quit", description="Exit program")
class QuitCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        return False


@command("/clear", "/c", description="Clear conversation history (auto-save)")
class ClearCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        saved = ctx.client.clear_history_with_save()
        if saved and ctx.display:
            ctx.display.info(f"Session saved: {saved.id}")

        os.system("cls" if os.name == "nt" else "clear")

        if ctx.display:
            ctx.display.welcome("mocode", ctx.client.config.display_name, os.getcwd())
            ctx.display.success("Conversation cleared")

        return True


@command("/", "/help", "/h", "/?", description="Select command")
class HelpCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        from .base import CommandRegistry

        registry = CommandRegistry()

        if not ctx.args:
            commands = registry.all()
            choices = [
                (cmd.name, f"{cmd.name} - {cmd.description}")
                for cmd in commands
                if cmd.name not in ("/help", "/")
            ]
            choices.append(MenuItem.exit_())

            selected = select("Select command", choices)

            if not is_cancelled(selected):
                ctx.args = selected
                return registry.execute(ctx)
            return True

        if ctx.display:
            ctx.display.info("Commands:")
            for cmd in registry.all():
                aliases = f" {DIM}({', '.join(cmd.aliases)}){RESET}" if cmd.aliases else ""
                ctx.display.command_output(f"  {DIM}{cmd.name}{RESET}{aliases:<12} {cmd.description}")

        return True

"""Command executor - handles command matching and execution"""

import asyncio

from .base import CommandContext, CommandRegistry
from ..ui.prompt import select, MenuItem, is_cancelled
from ..ui.display import Display


class CommandExecutor:
    """Command execution handler with fuzzy matching and menu selection."""

    def __init__(self, registry: CommandRegistry, display: Display):
        self._registry = registry
        self._display = display

    async def execute(self, ctx: CommandContext) -> bool:
        # Set event loop for async operations from commands
        ctx.loop = asyncio.get_running_loop()

        command_name = ctx.args.split()[0] if ctx.args else ""
        matches = self._registry.find_matches(command_name)

        if len(matches) == 0:
            self._display.command_output(f"Unknown command: {command_name}")
            return True

        if len(matches) == 1:
            cmd = matches[0]
            if cmd.name == "/exit":
                self._display.exit_message("Goodbye!")
                return False
            parts = ctx.args.split(maxsplit=1)
            ctx.args = parts[1] if len(parts) > 1 else ""
            return await asyncio.to_thread(cmd.execute, ctx)

        # Multiple matches: show menu
        choices = [
            (cmd.name, f"{cmd.name} - {cmd.description}")
            for cmd in matches
            if command_name != "/" or cmd.name != "/"
        ]
        choices.append(MenuItem.exit_())

        selected = select(f"Commands matching '{command_name}'", choices)

        if not is_cancelled(selected):
            parts = ctx.args.split(maxsplit=1)
            original_args = parts[1] if len(parts) > 1 else ""
            ctx.args = f"{selected} {original_args}" if original_args else selected
            return await asyncio.to_thread(self._registry.execute, ctx)

        return True

"""Command executor - handles command matching and execution"""

import asyncio

from ..commands.base import CommandContext, CommandRegistry
from ..ui import SelectMenu, is_cancelled
from ..ui.layout import Layout
from ..ui.menu import MenuItem


class CommandExecutor:
    """Command execution handler.

    Handles fuzzy matching, menu selection for ambiguous commands,
    and async execution wrapper.
    """

    def __init__(self, registry: CommandRegistry, layout: Layout):
        self._registry = registry
        self._layout = layout

    async def execute(self, ctx: CommandContext) -> bool:
        """Execute a command.

        Args:
            ctx: Command context with client, args, and layout

        Returns:
            True to continue running, False to exit
        """
        command_name = ctx.args.split()[0] if ctx.args else ""
        matches = self._registry.find_matches(command_name)

        if len(matches) == 0:
            self._layout.add_command_output(f"Unknown command: {command_name}")
            return True

        if len(matches) == 1:
            cmd = matches[0]
            # Check exit command
            if cmd.name == "/exit":
                self._layout.add_exit_message("Goodbye!")
                return False
            # Execute command with remaining args
            parts = ctx.args.split(maxsplit=1)
            ctx.args = parts[1] if len(parts) > 1 else ""
            return await asyncio.to_thread(cmd.execute, ctx)

        # Multiple matches: show menu
        # When showing all commands (prefix="/"), exclude "/" itself
        choices = [
            (cmd.name, f"{cmd.name} - {cmd.description}")
            for cmd in matches
            if command_name != "/" or cmd.name != "/"
        ]
        choices.append(MenuItem.exit_())

        selected = SelectMenu(f"Commands matching '{command_name}'", choices).show()

        if not is_cancelled(selected):
            # Preserve original arguments after selection
            parts = ctx.args.split(maxsplit=1)
            original_args = parts[1] if len(parts) > 1 else ""
            ctx.args = f"{selected} {original_args}" if original_args else selected
            return await asyncio.to_thread(self._registry.execute, ctx)

        return True

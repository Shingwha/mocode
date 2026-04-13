"""Command executor - dispatches commands, no UI dependency"""

import asyncio

from .base import CommandContext, CommandRegistry
from .result import CommandResult


class CommandExecutor:
    """Command execution handler with prefix matching."""

    def __init__(self, registry: CommandRegistry):
        self._registry = registry

    async def execute(self, ctx: CommandContext) -> CommandResult:
        ctx.loop = asyncio.get_running_loop()

        command_name = ctx.args.split()[0] if ctx.args else ""
        matches = self._registry.find_matches(command_name)

        if not matches:
            return CommandResult(
                success=False, message=f"Unknown command: {command_name}"
            )

        if len(matches) == 1:
            cmd = matches[0]
            if cmd.name == "/exit":
                return CommandResult(should_exit=True)
            parts = ctx.args.split(maxsplit=1)
            ctx.args = parts[1] if len(parts) > 1 else ""
            return await asyncio.to_thread(cmd.execute, ctx)

        # Multiple matches: take first match (non-interactive)
        # CLI wrappers handle interactive selection
        cmd = matches[0]
        parts = ctx.args.split(maxsplit=1)
        ctx.args = parts[1] if len(parts) > 1 else ""
        return await asyncio.to_thread(cmd.execute, ctx)

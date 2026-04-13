"""Mode management command"""

from .base import Command, CommandContext, command
from .result import CommandResult
from ..events import EventType


@command("/mode", description="Manage operation modes")
class ModeCommand(Command):
    """Manage operation modes (normal, yolo, etc.)"""

    def execute(self, ctx: CommandContext) -> CommandResult:
        args = ctx.args.strip()
        current = ctx.client.config.current_mode

        if args == "list":
            modes = {
                name: {"auto_approve": m.auto_approve}
                for name, m in ctx.client.config.modes.items()
            }
            return CommandResult(data={"modes": modes, "current": current})

        if args:
            return self._switch_mode(ctx, args)

        # No args - return available modes for interactive selection
        modes = {
            name: {"auto_approve": m.auto_approve}
            for name, m in ctx.client.config.modes.items()
        }
        return CommandResult(data={"modes": modes, "current": current})

    def _switch_mode(self, ctx: CommandContext, name: str) -> CommandResult:
        if not ctx.client.config.set_mode(name):
            return CommandResult(
                success=False,
                message=f"Unknown mode: {name}. Use /mode list to see available modes.",
            )
        mode = ctx.client.config.modes[name]
        ctx.client.event_bus.emit(EventType.STATUS_UPDATE, f"Mode switched to: {name}")
        return CommandResult(
            success=True,
            message=f"Switched to mode: {name} (auto-approve={mode.auto_approve})",
            data={"mode": name, "auto_approve": mode.auto_approve},
        )

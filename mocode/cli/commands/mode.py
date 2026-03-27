"""Mode management command"""

from .base import Command, CommandContext, command
from ...core.events import EventType


@command("/mode", description="Manage operation modes")
class ModeCommand(Command):
    """Manage operation modes (normal, yolo, etc.)"""

    def execute(self, ctx: CommandContext) -> bool:
        args = ctx.args.strip()

        if not args:
            current = ctx.client.config.current_mode
            modes = list(ctx.client.config.modes.keys())
            ctx.client.event_bus.emit(
                EventType.STATUS_UPDATE,
                f"Mode: {current} (available: {', '.join(modes)})"
            )
            if ctx.display:
                ctx.display.info(f"Current mode: {current}")
                ctx.display.info(f"Available modes: {', '.join(modes)}")
            return True

        if args == "list":
            modes = list(ctx.client.config.modes.items())
            current = ctx.client.config.current_mode
            if ctx.display:
                ctx.display.info("Available modes:")
                for name, mode in sorted(modes, key=lambda x: (x[0] != "normal", x[0] != "yolo", x[0])):
                    marker = "* " if name == current else "  "
                    desc = f"(auto-approve: {mode.auto_approve})"
                    ctx.display.command_output(f"{marker}{name} ({desc})")
            return True

        if ctx.client.config.set_mode(args):
            mode = ctx.client.config.modes[args]
            ctx.client.event_bus.emit(
                EventType.STATUS_UPDATE,
                f"Mode switched to: {args}"
            )
            if ctx.display:
                ctx.display.success(f"Switched to mode: {args} (auto-approve={mode.auto_approve})")
            return True
        else:
            if ctx.display:
                ctx.display.error(f"Unknown mode: {args}. Use /mode list to see available modes.")
            return True

"""Mode management command"""

from .base import Command, CommandContext, command
from ..ui.prompt import select, MenuItem, is_cancelled
from ..ui.styles import GREEN, DIM, RESET
from ...core.events import EventType


@command("/mode", description="Manage operation modes")
class ModeCommand(Command):
    """Manage operation modes (normal, yolo, etc.)"""

    def execute(self, ctx: CommandContext) -> bool:
        args = ctx.args.strip()
        current = ctx.client.config.current_mode

        if args == "list":
            return self._list_modes(ctx, current)

        if args:
            return self._switch_mode(ctx, args)

        return self._select_interactive(ctx, current)

    def _list_modes(self, ctx: CommandContext, current: str) -> bool:
        modes = list(ctx.client.config.modes.items())
        self._info(ctx, "Available modes:")
        for name, mode in sorted(modes, key=lambda x: (x[0] != "normal", x[0] != "yolo", x[0])):
            marker = f"{GREEN}*{RESET}" if name == current else " "
            self._output(ctx, f" {marker} {name} (auto-approve: {mode.auto_approve})")
        return True

    def _switch_mode(self, ctx: CommandContext, name: str) -> bool:
        if not ctx.client.config.set_mode(name):
            self._error(ctx, f"Unknown mode: {name}. Use /mode list to see available modes.")
            return True
        mode = ctx.client.config.modes[name]
        ctx.client.event_bus.emit(EventType.STATUS_UPDATE, f"Mode switched to: {name}")
        self._success(ctx, f"Switched to mode: {name} (auto-approve={mode.auto_approve})")
        return True

    def _select_interactive(self, ctx: CommandContext, current: str) -> bool:
        modes = list(ctx.client.config.modes.items())
        choices = [
            (name, f"{name} - auto-approve: {mode.auto_approve}")
            for name, mode in modes
        ]
        choices.append(MenuItem.exit_())

        selected = select("Select mode", choices, current=current)
        if not is_cancelled(selected):
            self._switch_mode(ctx, selected)
        return True

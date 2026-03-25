"""Mode management command"""

from ..ui import format_info, format_success, format_error
from .base import Command, CommandContext, command
from ...core.events import EventType  # 导入 EventType


@command("/mode", description="Manage operation modes")
class ModeCommand(Command):
    """Manage operation modes (normal, yolo, etc.)"""

    name = "/mode"
    # 支持 /mode list 作为子命令，通过 args 处理

    def execute(self, ctx: CommandContext) -> bool:
        args = ctx.args.strip()

        if not args:
            # 显示当前模式
            current = ctx.client.config.current_mode
            modes = list(ctx.client.config.modes.keys())
            ctx.client.event_bus.emit(
                EventType.STATUS_UPDATE,
                f"Mode: {current} (available: {', '.join(modes)})"
            )
            if ctx.layout:
                ctx.layout.add_command_output(format_info(f"Current mode: {current}"))
                ctx.layout.add_command_output(format_info(f"Available modes: {', '.join(modes)}"))
            return True

        if args == "list":
            # 列出所有可用模式
            modes = list(ctx.client.config.modes.items())
            current = ctx.client.config.current_mode
            lines = []
            for name, mode in sorted(modes, key=lambda x: (x[0] != "normal", x[0] != "yolo", x[0])):
                marker = f"{format_success('*')} " if name == current else "  "
                desc = f"(auto-approve: {mode.auto_approve})"
                lines.append(f"{marker}{name} {format_info(desc)}")
            if ctx.layout:
                ctx.layout.add_command_output(format_info("Available modes:"))
                for line in lines:
                    ctx.layout.add_command_output(line)
            return True

        # 切换模式
        if ctx.client.config.set_mode(args):
            mode = ctx.client.config.modes[args]
            ctx.client.event_bus.emit(
                EventType.STATUS_UPDATE,
                f"Mode switched to: {args}"
            )
            if ctx.layout:
                ctx.layout.add_command_output(
                    format_success(f"Switched to mode: {args} (auto-approve={mode.auto_approve})")
                )
            return True
        else:
            if ctx.layout:
                ctx.layout.add_command_output(
                    format_error(f"Unknown mode: {args}. Use /mode list to see available modes.")
                )
            return True

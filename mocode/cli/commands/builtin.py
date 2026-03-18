"""内置命令"""

from ..ui.colors import DIM, RESET
from ..ui.components import format_info, format_success
from .base import Command, CommandContext, command


@command("/exit", "q", "quit", description="退出程序")
class QuitCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        return False


@command("/clear", "c", description="清空对话历史 (自动保存)")
class ClearCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        saved = ctx.client.clear_history_with_save()
        if saved and ctx.layout:
            ctx.layout.add_command_output(format_info(f"Session saved: {saved.id}"))
        if ctx.layout:
            ctx.layout.add_command_output(format_success("Cleared conversation"))
        return True


@command("/", "/help", "/h", "/?", description="选择命令")
class HelpCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        from ..ui.widgets import SelectMenu
        from .base import CommandRegistry

        registry = CommandRegistry()

        # 如果没有参数，显示交互式选择菜单
        if not ctx.args:
            commands = registry.all()
            # 排除 /help 和 / 本身
            choices = [
                (cmd.name, f"{cmd.name} - {cmd.description}")
                for cmd in commands
                if cmd.name not in ("/help", "/")
            ]
            choices.append(("__EXIT__", f"{DIM}← Cancel{RESET}"))

            menu = SelectMenu("选择命令", choices)
            selected = menu.show()

            if selected == "__EXIT__":
                return True
            elif selected:
                # 执行选中的命令
                ctx.args = selected
                return registry.execute(ctx)
            return True

        # 有参数时显示帮助列表
        if ctx.layout:
            ctx.layout.add_command_output(format_info("Commands:"))
            for cmd in registry.all():
                aliases = (
                    f" {DIM}({', '.join(cmd.aliases)}){RESET}" if cmd.aliases else ""
                )
                ctx.layout.add_command_output(
                    f"  {DIM}{cmd.name}{RESET}{aliases:<12} {cmd.description}"
                )

        return True

"""Built-in commands"""

import os

from ..ui import SelectMenu, MenuAction, MenuItem, is_cancelled, format_info, format_success
from ..ui.colors import DIM, RESET
from .base import Command, CommandContext, command


@command("/exit", "/q", "/quit", description="Exit program")
class QuitCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        return False


@command("/clear", "/c", description="Clear conversation history (auto-save)")
class ClearCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        saved = ctx.client.clear_history_with_save()
        if saved and ctx.layout:
            ctx.layout.add_command_output(format_info(f"Session saved: {saved.id}"))

        # 清空终端屏幕
        os.system("cls" if os.name == "nt" else "clear")

        # 重新显示欢迎信息
        if ctx.layout:
            ctx.layout.show_welcome("mocode", ctx.client.config.display_name, os.getcwd())
            ctx.layout.add_command_output(format_success("Conversation cleared"))

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

            selected = SelectMenu("Select command", choices).show()

            if not is_cancelled(selected):
                ctx.args = selected
                return registry.execute(ctx)
            return True

        if ctx.layout:
            ctx.layout.add_command_output(format_info("Commands:"))
            for cmd in registry.all():
                aliases = f" {DIM}({', '.join(cmd.aliases)}){RESET}" if cmd.aliases else ""
                ctx.layout.add_command_output(f"  {DIM}{cmd.name}{RESET}{aliases:<12} {cmd.description}")

        return True

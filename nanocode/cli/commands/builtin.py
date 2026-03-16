"""内置命令"""

from .base import Command, CommandContext, command
from ..ui import success, info
from ..ui.colors import DIM, RESET


@command("/q", "exit", "quit", description="退出程序")
class QuitCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        return False


@command("/c", description="清空对话历史")
class ClearCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        ctx.agent.clear()
        success("Cleared conversation")
        return True


@command("/", "/help", "/h", "/?", description="显示帮助信息 / 选择命令")
class HelpCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        from .base import CommandRegistry
        from ..ui.widgets import SelectMenu
        from ..ui import success

        registry = CommandRegistry()
        
        # 如果没有参数，显示交互式选择菜单
        if not ctx.args:
            commands = registry.all()
            # 排除 /help 和 / 本身
            choices = [(cmd.name, f"{cmd.name} - {cmd.description}")
                      for cmd in commands
                      if cmd.name not in ("/help", "/")]

            menu = SelectMenu("选择命令", choices)
            selected = menu.show()
            
            if selected:
                # 执行选中的命令
                ctx.args = selected
                return registry.execute(ctx)
            return True
        
        # 有参数时显示帮助列表
        info("Commands:")
        for cmd in registry.all():
            aliases = f" {DIM}({', '.join(cmd.aliases)}){RESET}" if cmd.aliases else ""
            print(f"  {DIM}{cmd.name}{RESET}{aliases:<12} {cmd.description}")

        return True

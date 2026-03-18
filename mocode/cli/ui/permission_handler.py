"""CLI 权限处理器 - 使用 SelectMenu 进行交互"""

from ...core.permission import PermissionHandler
from .widgets import SelectMenu
from .colors import BLUE, BOLD, DIM, GREEN, RESET


class CLIPermissionHandler(PermissionHandler):
    """CLI 专用权限处理器

    使用 SelectMenu 与用户交互，提供允许/拒绝/自定义输入选项。
    """

    def __init__(self, layout=None):
        """初始化 CLI 权限处理器

        Args:
            layout: SimpleLayout 实例，用于输出格式化（可选）
        """
        self.layout = layout

    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        """询问用户权限

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            用户响应: "allow", "deny", 或自定义输入
        """
        # 提取目标用于显示
        target = (
            tool_args.get("cmd")
            or tool_args.get("command")
            or tool_args.get("path")
            or ""
        )

        # 生成标题
        if target:
            preview = target[:60] + "..." if len(target) > 60 else target
            title = f"Permission required for {GREEN}{tool_name}{RESET} ({DIM}{preview}{RESET})"
        else:
            title = f"Permission required for {GREEN}{tool_name}{RESET}"

        # 打印前置空行（如果有 layout）
        if self.layout:
            self.layout._spacing.print_space_if_needed("permission")

        # 显示选择菜单
        menu = SelectMenu(
            title=title,
            choices=[
                ("allow", "Allow (execute the tool)"),
                ("deny", "Deny (cancel the operation)"),
                ("input", "Type something (provide custom response)"),
            ],
        )

        result = menu.show()

        if result == "allow":
            return "allow"
        elif result == "deny" or result is None:
            return "deny"
        elif result == "input":
            # 获取用户输入
            try:
                print(f"\n{BOLD}{BLUE}>{RESET} ", end="", flush=True)
                user_input = input()
                return user_input
            except (KeyboardInterrupt, EOFError):
                return "deny"

        return "deny"

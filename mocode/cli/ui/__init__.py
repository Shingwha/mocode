"""CLI UI 组件"""

from .colors import RESET, BOLD, DIM, BLUE, CYAN, GREEN, YELLOW
from .components import error, info, success, warn, format_error, format_info, format_success, format_warn
from .layout import Layout
from .widgets import SelectMenu
from .permission_handler import CLIPermissionHandler
from .interactive import ask, Wizard, parse_selection_arg

__all__ = [
    # 颜色
    "RESET",
    "BOLD",
    "DIM",
    "BLUE",
    "CYAN",
    "GREEN",
    "YELLOW",
    # 消息函数
    "error",
    "success",
    "info",
    "warn",
    # 格式化函数（不打印）
    "format_error",
    "format_success",
    "format_info",
    "format_warn",
    # 布局
    "Layout",
    # 组件
    "SelectMenu",
    # 权限处理器
    "CLIPermissionHandler",
    # 交互式提示
    "ask",
    "Wizard",
    "parse_selection_arg",
]

# Backward compatibility alias
SimpleLayout = Layout
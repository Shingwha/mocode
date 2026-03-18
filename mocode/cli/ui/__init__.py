"""CLI UI 组件"""

from .colors import RESET, BOLD, DIM, BLUE, CYAN, GREEN, YELLOW, RED
from .components import error, info, success, format_error, format_info, format_success
from .layout import SimpleLayout
from .widgets import SelectMenu
from .permission_handler import CLIPermissionHandler

__all__ = [
    # 颜色
    "RESET",
    "BOLD",
    "DIM",
    "BLUE",
    "CYAN",
    "GREEN",
    "YELLOW",
    "RED",
    # 消息函数
    "error",
    "success",
    "info",
    # 格式化函数（不打印）
    "format_error",
    "format_success",
    "format_info",
    # 布局
    "SimpleLayout",
    # 组件
    "SelectMenu",
    # 权限处理器
    "CLIPermissionHandler",
]
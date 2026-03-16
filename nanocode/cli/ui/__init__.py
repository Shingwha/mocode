"""CLI UI 组件"""

from .colors import RESET, BOLD, DIM, BLUE, CYAN, GREEN, YELLOW, RED
from .components import error, info, success
from .layout import SimpleLayout
from .widgets import SelectMenu

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
    # 布局
    "SimpleLayout",
    # 组件
    "SelectMenu",
]
"""UI 组件 - 简洁的消息显示"""

from .colors import RESET, RED, GREEN, CYAN, YELLOW


def error(text: str):
    """显示错误消息"""
    print(f"{RED}✗{RESET} {text}")


def success(text: str):
    """显示成功消息"""
    print(f"{GREEN}✓{RESET} {text}")


def info(text: str):
    """显示信息消息"""
    print(f"{CYAN}ℹ{RESET} {text}")

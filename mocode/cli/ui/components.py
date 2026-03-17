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


def format_error(text: str) -> str:
    """格式化错误消息（不打印）"""
    return f"{RED}✗{RESET} {text}"


def format_success(text: str) -> str:
    """格式化成功消息（不打印）"""
    return f"{GREEN}✓{RESET} {text}"


def format_info(text: str) -> str:
    """格式化信息消息（不打印）"""
    return f"{CYAN}ℹ{RESET} {text}"

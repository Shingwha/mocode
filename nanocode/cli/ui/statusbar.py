"""状态栏组件"""

import shutil
import sys

from .colors import CYAN, DIM, RESET


class AdaptiveSeparator:
    """自适应分隔线"""

    @staticmethod
    def get(char: str = "─", max_width: int = 80) -> str:
        """获取适合当前终端宽度的分隔线"""
        try:
            width = shutil.get_terminal_size().columns
        except:
            width = 80
        width = min(width, max_width)
        return char * width


class StatusBar:
    """底部状态栏"""

    def __init__(self):
        self.enabled = True
        self._content = ""

    def set(self, content: str):
        """设置状态栏内容"""
        self._content = content

    def render(self) -> str:
        """渲染状态栏"""
        if not self._content:
            return ""
        return f"{DIM}{self._content}{RESET}"

    def clear(self):
        """清除状态栏"""
        self._content = ""

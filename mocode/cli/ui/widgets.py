"""交互式 UI 组件"""

import sys
from typing import Generic, TypeVar

from .colors import BOLD, CYAN, DIM, GREEN, RESET
from .keyboard import getch

T = TypeVar("T")

# 模块级暂停标志，用于在 SelectMenu 显示期间暂停 ESC 监听
_esc_monitor_paused = False


def pause_esc_monitor():
    """暂停 ESC 键监听"""
    global _esc_monitor_paused
    _esc_monitor_paused = True


def resume_esc_monitor():
    """恢复 ESC 键监听"""
    global _esc_monitor_paused
    _esc_monitor_paused = False


def check_esc_key() -> bool:
    """非阻塞检测 ESC 键

    Returns:
        True 如果检测到 ESC 键，False 否则
    """
    # 暂停时直接返回，不消费任何按键
    if _esc_monitor_paused:
        return False

    if sys.platform == "win32":
        import msvcrt

        if msvcrt.kbhit():
            ch = msvcrt.getch()
            # ESC 键
            if ch == b"\x1b":
                return True
        return False
    else:
        import select

        # 非阻塞检测标准输入是否有数据
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                return True
        return False


class SelectMenu(Generic[T]):
    """交互式选择菜单"""

    def __init__(self, title: str, choices: list[tuple[T, str]], current: T = None):
        self.title = title
        self.choices = choices
        self.current = current
        self.selected = 0

        if current:
            for i, (key, _) in enumerate(choices):
                if key == current:
                    self.selected = i
                    break

    def show(self) -> T | None:
        """显示菜单并返回选择结果"""
        pause_esc_monitor()
        try:
            self._render_initial()

            while True:
                try:
                    key = self._getch()
                    if key == "UP":
                        self.selected = (self.selected - 1) % len(self.choices)
                        self._render_update()
                    elif key == "DOWN":
                        self.selected = (self.selected + 1) % len(self.choices)
                        self._render_update()
                    elif key in ("\r", "\n", "RIGHT"):
                        return self.choices[self.selected][0]
                    elif key == "LEFT" or key == "\x1b":
                        return None
                except (KeyboardInterrupt, EOFError):
                    return None
        finally:
            resume_esc_monitor()

    def _render_initial(self):
        """首次渲染"""
        if self.title:
            print(f"{BOLD}{CYAN}?{RESET} {self.title}")
        for i, (key, display) in enumerate(self.choices):
            print(self._format_line(i, key, display))

    def _render_update(self):
        """更新渲染"""
        lines = len(self.choices) + (1 if self.title else 0)
        print(f"\033[{lines}A", end="")
        print("\033[J", end="")
        if self.title:
            print(f"{BOLD}{CYAN}?{RESET} {self.title}")
        for i, (key, display) in enumerate(self.choices):
            print(self._format_line(i, key, display))

    def _format_line(self, index: int, key: T, display: str) -> str:
        """格式化单行"""
        if index == self.selected:
            marker = f"{GREEN}>{RESET}"
            text = f"{BOLD}{display}{RESET}"
        elif key == self.current:
            marker = f"{DIM}*{RESET}"
            text = display
        else:
            marker = f"{DIM} {RESET}"
            text = f"{DIM}{display}{RESET}"
        return f"  {marker} {text}"

    def _getch(self) -> str:
        """获取按键（使用统一的 keyboard 模块）"""
        return getch(with_arrows=True)

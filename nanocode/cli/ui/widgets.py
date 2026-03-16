"""交互式 UI 组件"""

import sys
from typing import TypeVar, Generic

from .colors import RESET, BOLD, DIM, GREEN

T = TypeVar("T")


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

    def _render_initial(self):
        """首次渲染（不再自带空行）"""
        for i, (key, display) in enumerate(self.choices):
            print(self._format_line(i, key, display))

    def _render_update(self):
        """更新渲染"""
        lines = len(self.choices)  # 移除 +1（不再有前置空行）
        print(f"\033[{lines}A", end="")
        print("\033[J", end="")
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
        """跨平台获取按键"""
        if sys.platform == "win32":
            import msvcrt
            ch = msvcrt.getch()
            if ch == b'\xe0':
                ch = msvcrt.getch()
                return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(
                    ch.decode("latin-1"), ""
                )
            return ch.decode("utf-8", errors="ignore")
        else:
            import tty
            import termios
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    seq = sys.stdin.read(2)
                    return {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}.get(
                        seq, ""
                    )
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

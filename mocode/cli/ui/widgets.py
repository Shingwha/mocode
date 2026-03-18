"""交互式 UI 组件"""

import sys
from typing import Callable, Generic, TypeVar

from .colors import BOLD, CYAN, DIM, GREEN, RESET
from .keyboard import getch
from .navigation import Action

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


def clear_screen():
    """清屏（保留光标位置）"""
    # 清屏并移动光标到左上角
    print("\033[2J\033[H", end="")


class SelectMenu(Generic[T]):
    """交互式选择菜单"""

    def __init__(
        self,
        title: str,
        choices: list[tuple[T, str]],
        current: T = None,
        on_select: Callable[[T], Action | T | None] = None,
    ):
        """
        Args:
            title: 菜单标题
            choices: 选项列表 [(key, display), ...]
            current: 当前选中值
            on_select: 选择后的回调函数，接收选中的 key，返回：
                      - Action.BACK: 返回上一层级
                      - Action.STAY: 保持当前层级（重绘）
                      - Action.EXIT: 完全退出导航
                      - 其他值: 作为结果返回
                      - None: 默认行为（返回上一层级）
        """
        self.title = title
        self.choices = choices
        self.current = current
        self.selected = 0
        self.on_select = on_select

        if current:
            for i, (key, _) in enumerate(choices):
                if key == current:
                    self.selected = i
                    break

    def show(self) -> T | Action | None:
        """显示菜单并返回选择结果

        Returns:
            - 如果注册了 on_select:
              - Action: 导航控制信号
              - 其他: on_select 的返回值
            - 如果没有 on_select:
              - 选中的 key
              - None: 用户取消（ESC/LEFT）
        """
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
                        selected_key = self.choices[self.selected][0]

                        if self.on_select:
                            result = self.on_select(selected_key)

                            # 处理 Action 类型
                            if isinstance(result, Action):
                                if result is Action.STAY:
                                    self._render_update()  # 重绘当前
                                    continue
                                return result  # BACK or EXIT
                            return result  # 普通返回值

                        # 兼容旧代码：直接返回 key
                        return selected_key
                    elif key == "LEFT" or key == "ESC":
                        return Action.BACK  # 使用 Action 信号
                except (KeyboardInterrupt, EOFError):
                    return Action.EXIT
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

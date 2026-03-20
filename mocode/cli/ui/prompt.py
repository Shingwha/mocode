"""交互式提示组件 - SelectMenu, ask(), Wizard"""

import shutil
import sys
from contextlib import contextmanager
from typing import Callable, Generic, TypeVar

from .colors import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from .components import error
from .keyboard import getch
from .textwrap import display_width, wrap_text

T = TypeVar("T")

# 模块级暂停标志，用于在交互组件显示期间暂停 ESC 监听
_esc_monitor_paused = False


@contextmanager
def esc_paused():
    """上下文管理器：暂停 ESC 监听"""
    global _esc_monitor_paused
    _esc_monitor_paused = True
    try:
        yield
    finally:
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
    """交互式选择菜单

    Returns:
        - 选中的 key: 用户选择了某个选项
        - None: 用户取消（ESC/LEFT）
        - KeyboardInterrupt: Ctrl+C
    """

    def __init__(
        self,
        title: str,
        choices: list[tuple[T, str]],
        current: T = None,
        max_width: int | None = None,
    ):
        self.title = title
        self.choices = choices
        self.current = current
        self.selected = 0
        self.max_width = max_width
        self._wrapped_choices: list[list[str]] = []  # Cache wrapped lines
        self._prepare_wrapped_choices()

        if current:
            for i, (key, _) in enumerate(choices):
                if key == current:
                    self.selected = i
                    break

    def _get_effective_width(self) -> int | None:
        """Get effective max_width for wrapping."""
        if self.max_width is None:
            return None
        # Account for "  > " prefix (4 chars)
        return max(20, self.max_width - 4)

    def _prepare_wrapped_choices(self) -> None:
        """Pre-wrap all choices and cache results."""
        width = self._get_effective_width()
        self._wrapped_choices = []

        for _, display in self.choices:
            if width is None:
                lines = display.split('\n')
            else:
                lines = wrap_text(display, width)
            self._wrapped_choices.append(lines)

    def show(self) -> T | None:
        """显示菜单并返回选择结果

        Returns:
            - 选中的 key
            - None: 用户取消（ESC/LEFT）
        """
        with esc_paused():
            self._render_initial()

            while True:
                key = self._getch()
                if key == "UP":
                    self.selected = (self.selected - 1) % len(self.choices)
                    self._render_update()
                elif key == "DOWN":
                    self.selected = (self.selected + 1) % len(self.choices)
                    self._render_update()
                elif key in ("\r", "\n", "RIGHT"):
                    return self.choices[self.selected][0]
                elif key == "LEFT" or key == "ESC":
                    return None

    def _render_initial(self):
        """首次渲染"""
        if self.title:
            print(f"{BOLD}{CYAN}?{RESET} {self.title}")
        for i, (key, _) in enumerate(self.choices):
            for line in self._format_choice(i, key, self._wrapped_choices[i]):
                print(line)

    def _render_update(self):
        """更新渲染"""
        total_lines = sum(len(lines) for lines in self._wrapped_choices)
        if self.title:
            total_lines += 1

        print(f"\033[{total_lines}A", end="")
        print("\033[J", end="")

        if self.title:
            print(f"{BOLD}{CYAN}?{RESET} {self.title}")
        for i, (key, _) in enumerate(self.choices):
            for line in self._format_choice(i, key, self._wrapped_choices[i]):
                print(line)

    def _format_choice(self, index: int, key: T, lines: list[str]) -> list[str]:
        """Format a choice (potentially multi-line) with proper styling."""
        result = []

        for line_idx, line in enumerate(lines):
            if index == self.selected:
                marker = f"{GREEN}>{RESET}" if line_idx == 0 else " "
                text = f"{BOLD}{line}{RESET}"
            elif key == self.current:
                marker = f"{DIM}*{RESET}" if line_idx == 0 else " "
                text = line
            else:
                marker = f"{DIM} {RESET}" if line_idx == 0 else " "
                text = f"{DIM}{line}{RESET}"

            # Continuation marker for wrapped lines
            if line_idx == 0:
                result.append(f"  {marker} {text}")
            else:
                result.append(f"    {DIM}│{RESET} {text}")

        return result

    def _getch(self) -> str:
        """获取按键（使用统一的 keyboard 模块）"""
        return getch(with_arrows=True)


def _readline_with_esc() -> str | None:
    """Read a line with ESC support. Returns None if ESC pressed."""
    chars = []
    while True:
        ch = getch(with_arrows=False)
        if ch == "ESC":
            return None
        elif ch in ("\r", "\n"):
            print()  # New line after enter
            return "".join(chars)
        elif ch == "\x7f" or ch == "\x08":  # Backspace (Unix/Windows)
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)  # Erase character
        elif ch == "\x03":  # Ctrl+C
            return None
        elif ch:  # Printable character
            chars.append(ch)
            print(ch, end="", flush=True)


def ask(
    message: str = "",
    *,
    hint: str | None = None,
    default: str | None = None,
    required: bool = False,
    validator: Callable[[str], bool | str] | None = None,
) -> str | None:
    """
    Unified input prompt function with ESC support.

    Args:
        message: The prompt message to display
        hint: Optional hint text shown in dim color
        default: Default value if user presses Enter (implies not required)
        required: If True, empty input returns None
        validator: Optional validation function. Returns True for valid,
                   False for invalid (shows generic error), or str for custom error message

    Returns:
        The input string, default value, or None if cancelled (ESC/Ctrl+C)
    """
    if message:
        print(f"{BOLD}{CYAN}?{RESET} {message}")
    if hint:
        print(f"{DIM}  {hint}{RESET}")

    with esc_paused():
        try:
            print(f"{MAGENTA}>{RESET} ", end="", flush=True)
            value = _readline_with_esc()
        except (KeyboardInterrupt, EOFError):
            print(f"{YELLOW}Cancelled{RESET}")
            return None

    if value is None:
        print(f"{YELLOW}Cancelled{RESET}")
        return None  # ESC pressed

    value = value.strip()

    # Handle empty input
    if not value:
        if default is not None:
            return default
        if required:
            error("Value cannot be empty")
            return None
        return ""

    # Validate if provided
    if validator:
        result = validator(value)
        if result is True:
            return value
        if result is False:
            error("Invalid value")
            return None
        # result is a custom error message string
        error(result)
        return None

    return value


class Wizard:
    """Manages multi-step input flows with automatic cancellation tracking."""

    def __init__(self, title: str | None = None):
        self._title = title
        self._cancelled = False
        self._started = False

    @property
    def cancelled(self) -> bool:
        """Returns True if any step was cancelled."""
        return self._cancelled

    def step(
        self,
        message: str = "",
        *,
        hint: str | None = None,
        default: str | None = None,
        required: bool = False,
        validator: Callable[[str], bool | str] | None = None,
    ) -> str | None:
        """
        Execute one step of the wizard.

        Returns None and marks wizard as cancelled if input fails.
        Note: Empty input for optional fields returns "" (not None).
        None always indicates cancellation or validation failure.
        """
        if self._cancelled:
            return None

        # Show wizard title on first step
        if self._title and not self._started:
            print(f"{BOLD}{CYAN}?{RESET} {self._title}\n")
            self._started = True

        result = ask(
            message,
            hint=hint,
            default=default,
            required=required,
            validator=validator,
        )

        # None means cancelled (Ctrl+C) or validation failed
        if result is None:
            self._cancelled = True

        return result

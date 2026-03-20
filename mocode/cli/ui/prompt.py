"""交互式提示组件 - SelectMenu, ask(), Wizard"""

import shutil
import sys
from contextlib import contextmanager
from typing import Callable, Generic, TypeVar

from .colors import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from .components import error
from .keyboard import getch
from .textwrap import truncate_text

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
        page_size: int = 8,
    ):
        self.title = title
        self.choices = choices
        self.current = current
        self.selected = 0
        self.max_width = max_width
        self.page_size = page_size
        self.scroll_offset = 0

        if current:
            for i, (key, _) in enumerate(choices):
                if key == current:
                    self.selected = i
                    break

    def _get_effective_width(self) -> int:
        """Get effective width for text truncation."""
        terminal_width = shutil.get_terminal_size().columns
        # Account for "  > " prefix (4 chars)
        if self.max_width is None:
            return max(20, terminal_width - 4)
        return max(20, min(self.max_width, terminal_width) - 4)

    def _get_effective_page_size(self) -> int:
        """Calculate effective page size based on terminal height."""
        terminal_height = shutil.get_terminal_size().lines
        # Reserve: title(1) + bottom space for errors/prompts(2)
        available = max(3, terminal_height - 3)
        return min(self.page_size, available)

    def _get_visible_range(self) -> tuple[int, int]:
        """Calculate current visible option index range."""
        total = len(self.choices)
        page_size = self._get_effective_page_size()

        if total <= page_size:
            return 0, total

        # Ensure selected is within visible area
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + page_size:
            self.scroll_offset = self.selected - page_size + 1

        # Boundary check
        self.scroll_offset = max(0, min(self.scroll_offset, total - page_size))
        return self.scroll_offset, self.scroll_offset + page_size

    def _format_title(self) -> str:
        """Format title with position indicator."""
        terminal_width = shutil.get_terminal_size().columns
        page_size = self._get_effective_page_size()

        if len(self.choices) <= page_size:
            # Reserve 3 for "? " prefix
            max_title_width = terminal_width - 3
            title = truncate_text(self.title, max_title_width)
            return f"{BOLD}{CYAN}?{RESET} {title}"

        pos = self.selected + 1
        total = len(self.choices)
        pos_text = f" ({pos}/{total})"
        # Reserve 3 for "? " prefix + pos_text length
        max_title_width = terminal_width - 3 - len(pos_text)
        title = truncate_text(self.title, max_title_width)
        return f"{BOLD}{CYAN}?{RESET} {title} {DIM}{pos_text}{RESET}"

    def show(self) -> T | None:
        """显示菜单并返回选择结果

        Returns:
            - 选中的 key
            - None: 用户取消（ESC/LEFT）
        """
        with esc_paused():
            self._render()

            while True:
                key = self._getch()
                if key == "UP":
                    self.selected = (self.selected - 1) % len(self.choices)
                    self._render_update()
                elif key == "DOWN":
                    self.selected = (self.selected + 1) % len(self.choices)
                    self._render_update()
                elif key in ("\r", "\n", "RIGHT"):
                    result = self.choices[self.selected][0]
                    self._clear()
                    return result
                elif key == "LEFT" or key == "ESC":
                    self._clear()
                    return None

    def _get_rendered_lines(self) -> int:
        """Calculate how many lines were rendered."""
        start, end = self._get_visible_range()
        return (1 if self.title else 0) + (end - start)

    def _clear(self):
        """Clear menu content."""
        lines = self._get_rendered_lines()
        print(f"\033[{lines}A", end="")
        print("\033[J", end="")

    def _render(self):
        """Render menu."""
        start, end = self._get_visible_range()
        if self.title:
            print(self._format_title())
        for i in range(start, end):
            print(self._format_choice(i, self.choices[i][0], self.choices[i][1]))

    def _render_update(self):
        """Update render after selection change."""
        self._clear()
        self._render()

    def _format_choice(self, index: int, key: T, text: str) -> str:
        """Format a single-line choice."""
        width = self._get_effective_width()
        text = truncate_text(text, width)

        if index == self.selected:
            return f"  {GREEN}>{RESET} {BOLD}{text}{RESET}"
        elif key == self.current:
            return f"  {DIM}*{RESET} {text}"
        else:
            return f"  {DIM} {RESET} {DIM}{text}{RESET}"

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

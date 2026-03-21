"""Interactive prompt components - Backward compatible interface.

This module provides backward-compatible functions and classes that internally
use the new component system.
"""

import sys
from contextlib import contextmanager
from typing import Callable, Generic, TypeVar

from .colors import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from .components import Message, Input, Select, MessagePreset
from .keyboard import getch
from .textwrap import truncate_text

T = TypeVar("T")

# Module-level pause flag for ESC monitoring
_esc_monitor_paused = False


@contextmanager
def esc_paused():
    """Context manager: pause ESC monitoring."""
    global _esc_monitor_paused
    _esc_monitor_paused = True
    try:
        yield
    finally:
        _esc_monitor_paused = False


def check_esc_key() -> bool:
    """Non-blocking check for ESC key.

    Returns:
        True if ESC pressed, False otherwise
    """
    if _esc_monitor_paused:
        return False

    if sys.platform == "win32":
        import msvcrt
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b"\x1b":
                return True
        return False
    else:
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                return True
        return False


def clear_screen():
    """Clear screen (preserve cursor position)."""
    print("\033[2J\033[H", end="")


class SelectMenu(Generic[T]):
    """Interactive selection menu - backward compatible wrapper.

    Uses the new Select component internally.
    """

    def __init__(
        self,
        title: str,
        choices: list[tuple[T, str]],
        current: T = None,
        max_width: int | None = None,
        page_size: int = 8,
    ):
        """Initialize SelectMenu.

        Args:
            title: Menu title
            choices: List of (value, label) tuples
            current: Currently selected value
            max_width: Maximum text width
            page_size: Items per page
        """
        self._select = Select(
            title,
            choices,
            current=current,
            max_width=max_width,
            page_size=page_size
        )
        # Expose properties for backward compatibility
        self.title = title
        self.choices = choices
        self.current = current
        self.max_width = max_width
        self.page_size = page_size
        self.selected = 0
        self.scroll_offset = 0

        if current:
            for i, (key, _) in enumerate(choices):
                if key == current:
                    self.selected = i
                    break

    def show(self) -> T | None:
        """Show menu and return selection.

        Returns:
            Selected value or None if cancelled
        """
        result = self._select.show()
        # Sync state
        self.selected = self._select.selected
        self.scroll_offset = self._select.scroll_offset
        return result


def ask(
    message: str = "",
    *,
    hint: str | None = None,
    default: str | None = None,
    required: bool = False,
    validator: Callable[[str], bool | str] | None = None,
) -> str | None:
    """Unified input prompt with ESC support.

    Uses the new Input component internally.

    Args:
        message: Prompt message
        hint: Optional hint text
        default: Default value if Enter pressed
        required: If True, empty input returns None
        validator: Validation function

    Returns:
        Input string, default, or None if cancelled
    """
    inp = Input(
        message,
        hint=hint,
        default=default,
        required=required,
        validator=validator
    )
    return inp.show()


class Wizard:
    """Multi-step input flow manager with cancellation tracking."""

    def __init__(self, title: str | None = None):
        """Initialize wizard.

        Args:
            title: Optional wizard title
        """
        self._title = title
        self._cancelled = False
        self._started = False

    @property
    def cancelled(self) -> bool:
        """Whether any step was cancelled."""
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
        """Execute one wizard step.

        Args:
            message: Prompt message
            hint: Optional hint
            default: Default value
            required: Whether input is required
            validator: Validation function

        Returns:
            Input value or None if cancelled
        """
        if self._cancelled:
            return None

        # Show title on first step
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

        if result is None:
            self._cancelled = True

        return result

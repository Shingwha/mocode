"""Interactive prompt utilities for CLI"""

import sys
from typing import Callable, TypeVar

from .colors import BOLD, BLUE, DIM, RESET, YELLOW
from .components import error, info
from .widgets import pause_esc_monitor, resume_esc_monitor

T = TypeVar("T")


def _getch() -> str:
    """Cross-platform getch with ESC detection."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getch()
        if ch == b'\xe0':
            ch = msvcrt.getch()
            return ""  # Ignore arrow keys
        if ch == b'\x1b':
            return "ESC"
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
                # Check for arrow key sequence
                import select
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    seq = sys.stdin.read(2)
                    return ""  # Arrow key, ignore
                return "ESC"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _readline_with_esc() -> str | None:
    """Read a line with ESC support. Returns None if ESC pressed."""
    chars = []
    while True:
        ch = _getch()
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
        info(message)
    if hint:
        print(f"{DIM}  {hint}{RESET}")

    pause_esc_monitor()
    try:
        print(f"{BOLD}{BLUE}>{RESET} ", end="", flush=True)
        value = _readline_with_esc()
    except (KeyboardInterrupt, EOFError):
        print(f"{YELLOW}Cancelled{RESET}")
        return None
    finally:
        resume_esc_monitor()

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


def parse_selection_arg(
    arg: str,
    items: list[T],
    *,
    interactive_func: Callable[[], T | None] | None = None,
    error_handler: Callable[[str], None] | None = None,
) -> T | None:
    """
    Parse command argument for selection (supports index, direct value, or interactive).

    Args:
        arg: The command argument (empty string for interactive mode)
        items: List of items for index-based selection
        interactive_func: Function to call for interactive selection (when arg is empty)
        error_handler: Optional error handler for invalid selections

    Returns:
        Selected item or None if cancelled/invalid
    """
    if not arg:
        # No argument: enter interactive mode
        if interactive_func:
            return interactive_func()
        return None

    if arg.isdigit():
        # Numeric selection by index (1-based)
        num = int(arg)
        if 1 <= num <= len(items):
            return items[num - 1]
        if error_handler:
            error_handler(f"Invalid choice: {num}")
        return None

    # Direct value - check if it's in items
    if arg in items:
        return arg

    # Not found in items, return as-is for direct matching
    return arg

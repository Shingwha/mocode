"""Interactive prompt components - Backward compatible interface.

This module provides backward-compatible functions and classes that internally
use the new component system.
"""

from enum import Enum, auto
from typing import Callable, Generic, TypeVar

from .colors import BOLD, CYAN, DIM, GREEN, MAGENTA, RED, RESET, YELLOW
from .components import Message, Input, Select, MessagePreset
from .keyboard import getch, esc_paused, check_esc_key
from .textwrap import truncate_text

T = TypeVar("T")


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


# Menu system (merged from menu.py)

class MenuAction(Enum):
    """Standard menu action identifiers."""
    EXIT = auto()
    BACK = auto()
    MANAGE = auto()
    CONFIRM = auto()
    CANCEL = auto()
    ADD = auto()
    EDIT = auto()
    DELETE = auto()
    DONE = auto()
    DISABLED = auto()
    LIST = auto()
    INFO = auto()


class MenuItem:
    """Builder for menu items with consistent styling."""

    @staticmethod
    def exit_() -> tuple[MenuAction, str]:
        return (MenuAction.EXIT, f"{DIM}<- Cancel{RESET}")

    @staticmethod
    def back() -> tuple[MenuAction, str]:
        return (MenuAction.BACK, f"{DIM}<- Back{RESET}")

    @staticmethod
    def manage() -> tuple[MenuAction, str]:
        return (MenuAction.MANAGE, f"{DIM}Manage...{RESET}")

    @staticmethod
    def add(label: str = "Add new") -> tuple[MenuAction, str]:
        return (MenuAction.ADD, f"{GREEN}+ {label}{RESET}")

    @staticmethod
    def delete(label: str = "Delete") -> tuple[MenuAction, str]:
        return (MenuAction.DELETE, f"{RED}- {label}{RESET}")

    @staticmethod
    def edit(label: str = "Edit") -> tuple[MenuAction, str]:
        return (MenuAction.EDIT, f"{YELLOW}* {label}{RESET}")

    @staticmethod
    def confirm(label: str = "Yes, confirm") -> tuple[MenuAction, str]:
        return (MenuAction.CONFIRM, f"{RED}x {label}{RESET}")

    @staticmethod
    def cancel(label: str = "No, cancel") -> tuple[MenuAction, str]:
        return (MenuAction.CANCEL, f"{GREEN}o {label}{RESET}")

    @staticmethod
    def done(label: str = "Save and exit") -> tuple[MenuAction, str]:
        return (MenuAction.DONE, f"{GREEN}* {label}{RESET}")

    @staticmethod
    def disabled(display: str) -> tuple[MenuAction, str]:
        return (MenuAction.DISABLED, f"{DIM}{display}{RESET}")

    @staticmethod
    def list_(label: str = "List all") -> tuple[MenuAction, str]:
        return (MenuAction.LIST, label)

    @staticmethod
    def info(label: str = "View info") -> tuple[MenuAction, str]:
        return (MenuAction.INFO, label)


def is_action(result, action: MenuAction) -> bool:
    """Check if menu result matches an action."""
    return result == action


def is_cancelled(result) -> bool:
    """Check if user cancelled (exit/back/None)."""
    return result is None or result in (MenuAction.EXIT, MenuAction.BACK)


def confirm_dialog(title: str) -> bool:
    """Show yes/no confirmation dialog. Returns True if confirmed."""
    menu = SelectMenu(title, [MenuItem.confirm(), MenuItem.cancel()])
    result = menu.show()
    return result == MenuAction.CONFIRM

"""High-level prompt API for interactive commands."""

from enum import Enum, auto
from typing import Callable, Generic, TypeVar

from .components import Input, Select
from .keyboard import esc_paused, check_esc_key
from .styles import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW

T = TypeVar("T")


def select(
    title: str,
    choices: list[tuple[T, str]],
    current: T | None = None,
    max_width: int | None = None,
    page_size: int = 8,
) -> T | None:
    """Show selection menu and return chosen value, or None if cancelled."""
    menu = Select(title, choices, current=current, max_width=max_width, page_size=page_size)
    return menu.show()


def ask(
    message: str = "",
    *,
    hint: str | None = None,
    default: str | None = None,
    required: bool = False,
    validator: Callable[[str], bool | str] | None = None,
) -> str | None:
    """Show input prompt and return entered value, or None if cancelled."""
    inp = Input(message, hint=hint, default=default, required=required, validator=validator)
    return inp.show()


def confirm(title: str) -> bool:
    """Show yes/no confirmation dialog. Returns True if confirmed."""
    result = select(title, [
        MenuItem.confirm(),
        MenuItem.cancel(),
    ])
    return result == MenuAction.CONFIRM


class Wizard:
    """Multi-step input flow with cancellation tracking."""

    def __init__(self, title: str | None = None):
        self._title = title
        self._cancelled = False
        self._started = False

    @property
    def cancelled(self) -> bool:
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
        """Execute one wizard step. Returns None if cancelled."""
        if self._cancelled:
            return None

        if self._title and not self._started:
            print(f"{BOLD}{CYAN}?{RESET} {self._title}\n")
            self._started = True

        result = ask(message, hint=hint, default=default, required=required, validator=validator)
        if result is None:
            self._cancelled = True
        return result


# --- Menu system ---

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
    """Check if user cancelled (None, EXIT, or BACK)."""
    return result is None or result in (MenuAction.EXIT, MenuAction.BACK)


def clear_screen() -> None:
    """Clear terminal screen."""
    print("\033[2J\033[H", end="")

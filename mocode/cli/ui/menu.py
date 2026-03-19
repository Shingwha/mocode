"""Unified menu system with typed constants."""

from enum import Enum, auto
from .colors import RESET, GREEN, YELLOW, RED, DIM
from .prompt import SelectMenu


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

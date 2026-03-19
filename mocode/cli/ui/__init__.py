"""CLI UI components"""

from .colors import RESET, BOLD, DIM, BLUE, CYAN, GREEN, YELLOW
from .components import (
    MessageType,
    error,
    info,
    success,
    warn,
    format_error,
    format_info,
    format_success,
    format_warn,
    format_message,
    print_message,
)
from .layout import Layout
from .prompt import SelectMenu, ask, Wizard, clear_screen
from .menu import MenuAction, MenuItem, is_action, is_cancelled, confirm_dialog
from .permission import CLIPermissionHandler
from .utils import parse_selection_arg

__all__ = [
    # Colors
    "RESET",
    "BOLD",
    "DIM",
    "BLUE",
    "CYAN",
    "GREEN",
    "YELLOW",
    # Message functions
    "error",
    "success",
    "info",
    "warn",
    # Format functions (no print)
    "format_error",
    "format_success",
    "format_info",
    "format_warn",
    "format_message",
    "print_message",
    "MessageType",
    # Layout
    "Layout",
    # Interactive prompts
    "SelectMenu",
    "ask",
    "Wizard",
    "clear_screen",
    # Menu system
    "MenuAction",
    "MenuItem",
    "is_action",
    "is_cancelled",
    "confirm_dialog",
    # Permission handler
    "CLIPermissionHandler",
    # Utils
    "parse_selection_arg",
]

# Backward compatibility alias
SimpleLayout = Layout
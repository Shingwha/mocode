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
from .widgets import SelectMenu
from .menu import MenuAction, MenuItem, is_action, is_cancelled, confirm_dialog
from .permission_handler import CLIPermissionHandler
from .interactive import ask, Wizard, parse_selection_arg

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
    # Components
    "SelectMenu",
    # Menu system
    "MenuAction",
    "MenuItem",
    "is_action",
    "is_cancelled",
    "confirm_dialog",
    # Permission handler
    "CLIPermissionHandler",
    # Interactive prompts
    "ask",
    "Wizard",
    "parse_selection_arg",
]

# Backward compatibility alias
SimpleLayout = Layout
"""CLI UI components - clean public API."""

# Display manager
from .display import Display, SpacingManager

# Interactive components
from .components import Input, Select, MultiSelect, Spinner

# High-level prompt API
from .prompt import (
    select,
    ask,
    confirm,
    Wizard,
    MenuAction,
    MenuItem,
    is_action,
    is_cancelled,
    clear_screen,
)

# Permission handler
from .permission import CLIPermissionHandler

# Colors and styles
from .styles import (
    RESET, BOLD, DIM,
    RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE,
    BG_RED, BG_GREEN, BG_YELLOW, BG_BLUE, BG_MAGENTA, BG_CYAN,
    MessagePreset,
    format_preset,
)

# Utilities
from .keyboard import esc_paused, check_esc_key
from .textwrap import display_width, wrap_text, truncate_text

__all__ = [
    # Display
    "Display",
    "SpacingManager",
    # Components
    "Input",
    "Select",
    "MultiSelect",
    "Spinner",
    # Prompt API
    "select",
    "ask",
    "confirm",
    "Wizard",
    "MenuAction",
    "MenuItem",
    "is_action",
    "is_cancelled",
    "clear_screen",
    # Permission
    "CLIPermissionHandler",
    # Colors
    "RESET",
    "BOLD",
    "DIM",
    "RED",
    "GREEN",
    "YELLOW",
    "BLUE",
    "MAGENTA",
    "CYAN",
    "WHITE",
    "BG_RED",
    "BG_GREEN",
    "BG_YELLOW",
    "BG_BLUE",
    "BG_MAGENTA",
    "BG_CYAN",
    # Styles
    "MessagePreset",
    "format_preset",
    # Keyboard
    "esc_paused",
    "check_esc_key",
    # Text
    "display_width",
    "wrap_text",
    "truncate_text",
]

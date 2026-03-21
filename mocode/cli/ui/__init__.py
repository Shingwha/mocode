"""CLI UI components"""

# Core base classes
from .base import Component, ComponentState, ComponentStyle
from .coordinator import ComponentCoordinator, SpacingRule

# Colors
from .colors import RESET, BOLD, DIM, BLUE, CYAN, GREEN, YELLOW, RED, WHITE, MAGENTA
from .colors import BG_BLUE, BG_GREEN, BG_RED, BG_CYAN, BG_MAGENTA, BG_YELLOW

# Components (from components package)
from .components import (
    MessageType,
    Message,
    MessagePreset,
    MESSAGE_STYLES,
    Input,
    Select,
    Animated,
    error,
    info,
    success,
    warn,
    format_message,
    print_message,
)

# Additional format functions for backward compatibility
def format_error(text: str) -> str:
    """Format error message."""
    return format_message(text, MessagePreset.ERROR)


def format_success(text: str) -> str:
    """Format success message."""
    return format_message(text, MessagePreset.SUCCESS)


def format_info(text: str) -> str:
    """Format info message."""
    return format_message(text, MessagePreset.INFO)


def format_warn(text: str) -> str:
    """Format warning message."""
    return format_message(text, MessagePreset.WARN)


# Layout
from .layout import Layout

# Interactive prompts (backward compatible)
from .prompt import SelectMenu, ask, Wizard, clear_screen, esc_paused, check_esc_key

# Menu system
from .menu import MenuAction, MenuItem, is_action, is_cancelled, confirm_dialog

# Permission handler
from .permission import CLIPermissionHandler

# Utils
from .utils import parse_selection_arg

__all__ = [
    # Base classes
    "Component",
    "ComponentState",
    "ComponentStyle",
    "ComponentCoordinator",
    "SpacingRule",
    # Colors
    "RESET",
    "BOLD",
    "DIM",
    "BLUE",
    "CYAN",
    "GREEN",
    "YELLOW",
    "RED",
    "WHITE",
    "MAGENTA",
    "BG_BLUE",
    "BG_GREEN",
    "BG_RED",
    "BG_CYAN",
    "BG_MAGENTA",
    "BG_YELLOW",
    # Components
    "MessageType",
    "Message",
    "MessagePreset",
    "MESSAGE_STYLES",
    "Input",
    "Select",
    "Animated",
    # Message functions
    "error",
    "success",
    "info",
    "warn",
    # Format functions
    "format_error",
    "format_success",
    "format_info",
    "format_warn",
    "format_message",
    "print_message",
    # Layout
    "Layout",
    # Interactive prompts
    "SelectMenu",
    "ask",
    "Wizard",
    "clear_screen",
    "esc_paused",
    "check_esc_key",
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

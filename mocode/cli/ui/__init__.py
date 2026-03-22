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
    MultiSelect,
    Animated,
    error,
    info,
    success,
    warn,
    format_error,
    format_success,
    format_info,
    format_warn,
    format_message,
    print_message,
)

# Layout
from .layout import Layout

# Interactive prompts (backward compatible)
from .prompt import (
    SelectMenu,
    ask,
    Wizard,
    clear_screen,
    esc_paused,
    check_esc_key,
    MenuAction,
    MenuItem,
    is_action,
    is_cancelled,
    confirm_dialog,
)

# Permission handler
from .permission import CLIPermissionHandler

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
    "MultiSelect",
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
]

# Backward compatibility alias
SimpleLayout = Layout

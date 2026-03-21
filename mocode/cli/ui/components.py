"""UI components - Backward compatible interface.

This module provides backward-compatible functions and types that internally
use the new component system.
"""

from .components import (
    MessageType,
    Message,
    MessagePreset,
    MESSAGE_STYLES,
    Input,
    Select,
    Animated,
    format_message,
    print_message,
    error,
    success,
    info,
    warn,
)


# Backward compatible format functions
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


# Export all
__all__ = [
    # Backward compatible
    "MessageType",
    "format_message",
    "print_message",
    "error",
    "success",
    "info",
    "warn",
    "format_error",
    "format_success",
    "format_info",
    "format_warn",
    # New components
    "Message",
    "MessagePreset",
    "MESSAGE_STYLES",
    "Input",
    "Select",
    "Animated",
]

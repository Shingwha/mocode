"""UI Components package."""

from ..colors import CYAN, GREEN, RED, YELLOW
from .styles import (
    MessagePreset,
    MessageStyle,
    MESSAGE_STYLES,
    InputStyle,
    DEFAULT_INPUT_STYLE,
    SelectStyle,
    DEFAULT_SELECT_STYLE,
    AnimatedStyle,
    DEFAULT_ANIMATED_STYLE,
)
from .message import Message, format_message, print_message, error, success, info, warn
from .input import Input
from .select import Select
from .animated import Animated

# Backward compatible MessageType enum
from enum import Enum


class MessageType(Enum):
    """Message types for styling - backward compatible."""

    ERROR = ("x", RED)
    SUCCESS = ("*", GREEN)
    INFO = ("->", CYAN)
    WARN = ("!", YELLOW)


def _convert_message_type(msg_type: MessageType) -> MessagePreset:
    """Convert old MessageType to new MessagePreset."""
    mapping = {
        MessageType.ERROR: MessagePreset.ERROR,
        MessageType.SUCCESS: MessagePreset.SUCCESS,
        MessageType.INFO: MessagePreset.INFO,
        MessageType.WARN: MessagePreset.WARN,
    }
    return mapping.get(msg_type, MessagePreset.INFO)


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


__all__ = [
    # Styles
    "MessagePreset",
    "MessageStyle",
    "MESSAGE_STYLES",
    "InputStyle",
    "DEFAULT_INPUT_STYLE",
    "SelectStyle",
    "DEFAULT_SELECT_STYLE",
    "AnimatedStyle",
    "DEFAULT_ANIMATED_STYLE",
    # Components
    "Message",
    "Input",
    "Select",
    "Animated",
    # Convenience functions
    "format_message",
    "print_message",
    "error",
    "success",
    "info",
    "warn",
    # Format functions (backward compatible)
    "format_error",
    "format_success",
    "format_info",
    "format_warn",
    # Backward compatible
    "MessageType",
]

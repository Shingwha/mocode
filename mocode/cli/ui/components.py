"""UI components - Unified message handling."""

from enum import Enum
from .colors import CYAN, GREEN, RED, RESET, YELLOW


class MessageType(Enum):
    """Message types for styling."""
    ERROR = ("x", RED)
    SUCCESS = ("*", GREEN)
    INFO = ("->", CYAN)
    WARN = ("!", YELLOW)


def format_message(text: str, msg_type: MessageType) -> str:
    """Format a message with type-specific styling."""
    symbol, color = msg_type.value
    return f"{color}{symbol}{RESET} {text}"


def print_message(text: str, msg_type: MessageType) -> None:
    """Format and print a message."""
    print(format_message(text, msg_type))


# Convenience functions
def error(text: str) -> None:
    print_message(text, MessageType.ERROR)


def success(text: str) -> None:
    print_message(text, MessageType.SUCCESS)


def info(text: str) -> None:
    print_message(text, MessageType.INFO)


def warn(text: str) -> None:
    print_message(text, MessageType.WARN)


# Format-only versions (return string, no print)
def format_error(text: str) -> str:
    return format_message(text, MessageType.ERROR)


def format_success(text: str) -> str:
    return format_message(text, MessageType.SUCCESS)


def format_info(text: str) -> str:
    return format_message(text, MessageType.INFO)


def format_warn(text: str) -> str:
    return format_message(text, MessageType.WARN)

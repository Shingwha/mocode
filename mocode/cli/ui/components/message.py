"""Message component for displaying styled messages."""

from typing import Any

from ..base import Component, ComponentState, ComponentStyle
from .styles import MessagePreset, MessageStyle, MESSAGE_STYLES


class Message(Component):
    """Message component with state-driven styling.

    Supports preset styles (ERROR, SUCCESS, INFO, WARN, QUESTION) and
    optional style change on completion.
    """

    def __init__(
        self,
        text: str,
        preset: MessagePreset = MessagePreset.INFO,
        style: MessageStyle | None = None,
        type_hint: str = ""
    ):
        """Initialize Message component.

        Args:
            text: Message text to display
            preset: Message preset type
            style: Custom style (uses preset default if None)
            type_hint: Type hint for spacing
        """
        super().__init__(type_hint=type_hint or "message")
        self.text = text
        self.preset = preset
        self._style_config = style or MESSAGE_STYLES.get(preset, MessageStyle(
            active=ComponentStyle(symbol="", color="")
        ))
        self._current_style = self._style_config.active

    @property
    def style(self) -> ComponentStyle:
        """Current active style."""
        return self._current_style

    def render(self) -> str:
        """Render message with current style.

        Returns:
            Formatted message string
        """
        symbol = self._current_style.format_symbol()
        if symbol:
            return f"{symbol} {self.text}"
        return self.text

    def complete_with_style(self, result: Any = None, clear_first: bool = False) -> None:
        """Complete and switch to completed style.

        Args:
            result: Optional result value
            clear_first: Whether to clear before showing completed style
        """
        if clear_first and self._rendered_lines > 0:
            self.print_clear()

        self._result = result

        # Switch to completed style if available
        if self._style_config.completed:
            self._current_style = self._style_config.completed
            content = self.render()
            self._rendered_lines = 1
            print(content)

        self.set_state(ComponentState.COMPLETED)

    def show(self) -> None:
        """Show message and return immediately.

        Returns:
            None (messages don't have results)
        """
        content = self.render()
        self._rendered_lines = 1
        print(content)
        self.set_state(ComponentState.COMPLETED)
        return None


# Convenience functions (compatibility with old components.py)

def format_message(text: str, preset: MessagePreset) -> str:
    """Format a message with preset styling.

    Args:
        text: Message text
        preset: Message preset type

    Returns:
        Formatted message string
    """
    msg = Message(text, preset)
    return msg.render()


def print_message(text: str, preset: MessagePreset) -> None:
    """Format and print a message.

    Args:
        text: Message text
        preset: Message preset type
    """
    msg = Message(text, preset)
    msg.show()


def error(text: str) -> None:
    """Print error message."""
    print_message(text, MessagePreset.ERROR)


def success(text: str) -> None:
    """Print success message."""
    print_message(text, MessagePreset.SUCCESS)


def info(text: str) -> None:
    """Print info message."""
    print_message(text, MessagePreset.INFO)


def warn(text: str) -> None:
    """Print warning message."""
    print_message(text, MessagePreset.WARN)


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

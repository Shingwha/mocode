"""Input component for user text input."""

import shutil
from typing import Callable

from ..base import Component, ComponentState
from ..colors import RESET
from ..keyboard import getch
from ..textwrap import display_width
from .styles import InputStyle, DEFAULT_INPUT_STYLE


class Input(Component):
    """Input component with completion highlighting.

    Features:
    - Prompt with hint support
    - Optional validation
    - Default value support
    - Blue background highlight on completion
    - ESC to cancel
    """

    def __init__(
        self,
        message: str = "",
        *,
        hint: str | None = None,
        default: str | None = None,
        required: bool = False,
        validator: Callable[[str], bool | str] | None = None,
        style: InputStyle | None = None,
        type_hint: str = "user_input"
    ):
        """Initialize Input component.

        Args:
            message: Prompt message to display
            hint: Optional hint text (dimmed)
            default: Default value if user presses Enter
            required: If True, empty input returns None
            validator: Validation function returning True/False/error string
            style: Custom style configuration
            type_hint: Type hint for spacing coordinator
        """
        super().__init__(type_hint=type_hint)
        self.message = message
        self.hint = hint
        self.default = default
        self.required = required
        self.validator = validator
        self._style = style or DEFAULT_INPUT_STYLE
        self._value: str | None = None

    @property
    def value(self) -> str | None:
        """Input value (available after completion)."""
        return self._value

    def render(self) -> str:
        """Render prompt (not used for interactive input).

        Returns:
            Prompt string
        """
        parts = []
        style = self._style

        # Title line
        if self.message:
            symbol = f"{style.prompt_style}{style.prompt_color}{style.prompt_symbol}{RESET}"
            parts.append(f"{symbol} {self.message}")

        # Hint line
        if self.hint:
            parts.append(f"{style.hint_style}  {self.hint}{RESET}")

        return "\n".join(parts)

    def show(self) -> str | None:
        """Show input prompt and get user input.

        Returns:
            Input string, default, or None if cancelled
        """
        style = self._style

        # Render prompt
        prompt_lines = []
        if self.message:
            prompt_lines.append(f"{style.prompt_style}{style.prompt_color}{style.prompt_symbol}{RESET} {self.message}")
        if self.hint:
            prompt_lines.append(f"{style.hint_style}  {self.hint}{RESET}")

        for line in prompt_lines:
            print(line)

        self._rendered_lines = len(prompt_lines)

        # Get input
        print(f"{style.input_color}{style.input_indicator}{RESET} ", end="", flush=True)

        try:
            value = self._readline_with_esc()
        except (KeyboardInterrupt, EOFError):
            self._cancel()
            return None

        if value is None:
            self._cancel()
            return None

        value = value.strip()

        # Handle empty input
        if not value:
            if self.default is not None:
                value = self.default
            elif self.required:
                from .message import error
                error("Value cannot be empty")
                self._rendered_lines += 1
                return None
            else:
                value = ""

        # Validate
        if self.validator and value:
            result = self.validator(value)
            if result is True:
                pass  # Valid
            elif result is False:
                from .message import error
                error("Invalid value")
                self._rendered_lines += 1
                return None
            else:
                # result is error message string
                from .message import error
                error(result)
                self._rendered_lines += 1
                return None

        # Complete with highlight
        self._complete_with_highlight(value)
        return value

    def _readline_with_esc(self) -> str | None:
        """Read a line with ESC support.

        Returns:
            Input string or None if ESC pressed
        """
        import sys

        chars = []
        while True:
            ch = getch(with_arrows=False)
            if ch == "ESC":
                return None
            elif ch in ("\r", "\n"):
                print()  # New line after enter
                return "".join(chars)
            elif ch == "\x7f" or ch == "\x08":  # Backspace (Unix/Windows)
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
            elif ch == "\x03":  # Ctrl+C
                return None
            elif ch:  # Printable character
                chars.append(ch)
                print(ch, end="", flush=True)

        return None

    def _cancel(self) -> None:
        """Handle cancellation."""
        from ..colors import YELLOW
        print(f"{YELLOW}Cancelled{RESET}")
        self._rendered_lines += 1
        self.set_state(ComponentState.COMPLETED)

    def _complete_with_highlight(self, value: str) -> None:
        """Complete with blue background highlight.

        Args:
            value: Final input value
        """
        self._value = value

        if not value.strip():
            self.set_state(ComponentState.COMPLETED)
            return

        # Calculate how many lines the input occupied
        terminal_width = shutil.get_terminal_size().columns
        prefix = f"{self._style.input_indicator} "
        prefix_width = len(prefix)

        text_width = display_width(value)
        total_width = prefix_width + text_width
        num_lines = max(1, (total_width + terminal_width - 1) // terminal_width)

        # Move cursor up to the start of input
        print(f"\033[{num_lines}A\r\033[K", end="")

        # Print highlighted line
        from ..colors import BG_BLUE, BOLD, WHITE, RESET
        display_line = f"{BG_BLUE}{BOLD}{WHITE}{prefix}{value} {RESET}"

        # Add padding to fill the line(s)
        padding_width = terminal_width * num_lines - text_width - prefix_width
        padding = " " * max(0, padding_width)

        print(f"{display_line}{padding}{RESET}")

        # Update rendered lines (input is now shown with highlight)
        self._rendered_lines = num_lines
        self.set_state(ComponentState.COMPLETED)

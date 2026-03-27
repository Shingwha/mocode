"""ANSI colors and style configurations."""

from dataclasses import dataclass
from enum import Enum

# ANSI escape codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Foreground colors
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

# Background colors
BG_RED = "\033[41m"
BG_GREEN = "\033[42m"
BG_YELLOW = "\033[43m"
BG_BLUE = "\033[44m"
BG_MAGENTA = "\033[45m"
BG_CYAN = "\033[46m"


class MessagePreset(Enum):
    """Message preset types."""

    ERROR = ("x", RED, "")
    SUCCESS = ("*", GREEN, "")
    INFO = ("○", CYAN, "")
    WARN = ("!", YELLOW, "")


# Message format helpers

def format_preset(text: str, preset: MessagePreset) -> str:
    """Format text with a message preset style."""
    symbol, color, _ = preset.value
    return f"{color}{symbol}{RESET} {text}"


# Component style dataclasses

@dataclass
class InputStyle:
    """Style for Input component."""
    prompt_symbol: str = "?"
    prompt_color: str = CYAN
    prompt_style: str = BOLD
    input_indicator: str = ">"
    input_color: str = MAGENTA
    completed_bg: str = BG_BLUE
    completed_text_style: str = BOLD
    completed_text_color: str = WHITE
    hint_style: str = DIM


@dataclass
class SelectStyle:
    """Style for Select/MultiSelect component."""
    title_symbol: str = "?"
    title_color: str = CYAN
    title_style: str = BOLD
    selected_indicator: str = ">"
    selected_color: str = GREEN
    selected_style: str = BOLD
    current_indicator: str = "*"
    current_color: str = DIM
    normal_style: str = DIM
    position_style: str = DIM


@dataclass
class AnimatedStyle:
    """Style for Spinner component."""
    frames: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    color: str = CYAN
    text_style: str = DIM
    interval: float = 0.08


DEFAULT_INPUT_STYLE = InputStyle()
DEFAULT_SELECT_STYLE = SelectStyle()
DEFAULT_ANIMATED_STYLE = AnimatedStyle()


# Convenience print functions (used by commands for inline output)

def error(text: str) -> None:
    """Print error message."""
    print(format_preset(text, MessagePreset.ERROR))


def success(text: str) -> None:
    """Print success message."""
    print(format_preset(text, MessagePreset.SUCCESS))


def info(text: str) -> None:
    """Print info message."""
    print(format_preset(text, MessagePreset.INFO))


def warn(text: str) -> None:
    """Print warning message."""
    print(format_preset(text, MessagePreset.WARN))

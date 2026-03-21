"""Preset styles for UI components."""

from dataclasses import dataclass
from enum import Enum

from ..colors import (
    RED, GREEN, CYAN, YELLOW, MAGENTA, BLUE, WHITE,
    BOLD, DIM, BG_BLUE, BG_GREEN, BG_RED
)
from ..base import ComponentStyle


class MessagePreset(Enum):
    """Message preset types with default styling."""

    ERROR = ("x", RED, "")
    SUCCESS = ("*", GREEN, "")
    INFO = ("○", CYAN, "")
    WARN = ("!", YELLOW, "")
    QUESTION = ("?", CYAN, BOLD)


@dataclass
class MessageStyle:
    """Style configuration for Message component."""

    active: ComponentStyle
    completed: ComponentStyle | None = None  # None means no style change on complete


# Default message styles for each preset
MESSAGE_STYLES: dict[MessagePreset, MessageStyle] = {
    MessagePreset.ERROR: MessageStyle(
        active=ComponentStyle(symbol="x", color=RED),
        completed=ComponentStyle(symbol="x", color=RED, bg_color=BG_RED)
    ),
    MessagePreset.SUCCESS: MessageStyle(
        active=ComponentStyle(symbol="*", color=GREEN),
        completed=ComponentStyle(symbol="*", color=GREEN, bg_color=BG_GREEN)
    ),
    MessagePreset.INFO: MessageStyle(
        active=ComponentStyle(symbol="○", color=CYAN)
    ),
    MessagePreset.WARN: MessageStyle(
        active=ComponentStyle(symbol="!", color=YELLOW)
    ),
    MessagePreset.QUESTION: MessageStyle(
        active=ComponentStyle(symbol="?", color=CYAN, text_style=BOLD)
    ),
}


@dataclass
class InputStyle:
    """Style configuration for Input component."""

    prompt_symbol: str = "?"
    prompt_color: str = CYAN
    prompt_style: str = BOLD
    input_indicator: str = ">"
    input_color: str = MAGENTA
    # Completed state
    completed_bg: str = BG_BLUE
    completed_text_style: str = BOLD
    completed_text_color: str = WHITE
    # Hint styling
    hint_style: str = DIM


DEFAULT_INPUT_STYLE = InputStyle()


@dataclass
class SelectStyle:
    """Style configuration for Select component."""

    title_symbol: str = "?"
    title_color: str = CYAN
    title_style: str = BOLD
    selected_indicator: str = ">"
    selected_color: str = GREEN
    selected_style: str = BOLD
    current_indicator: str = "*"
    current_color: str = DIM
    normal_style: str = DIM
    # Position indicator
    position_style: str = DIM


DEFAULT_SELECT_STYLE = SelectStyle()


@dataclass
class AnimatedStyle:
    """Style configuration for Animated component."""

    frames: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    color: str = CYAN
    text_style: str = DIM
    interval: float = 0.08  # seconds


DEFAULT_ANIMATED_STYLE = AnimatedStyle()

"""mocode.app.cli — interactive CLI application."""

from __future__ import annotations

from .app import CLIApp
from .commands import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from .display import Display
from .hook import CLIDisplayHook
from .spinner import Priority, Spinner, Truncate
from .theme import ColorPalette, DisplayStyles, Style, Theme

__all__ = [
    "CLIApp",
    "CLIDisplayHook",
    "CONTINUE",
    "ColorPalette",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "Display",
    "DisplayStyles",
    "EXIT",
    "Kind",
    "Priority",
    "Spinner",
    "Style",
    "Theme",
    "Truncate",
]

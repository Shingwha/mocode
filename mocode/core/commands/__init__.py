"""Core command system - UI-independent command infrastructure"""

from .result import CommandResult
from .base import Command, CommandContext, CommandRegistry, command
from .executor import CommandExecutor
from .utils import resolve_selection, parse_selection_arg
from .builtin import QuitCommand, ClearCommand, HelpCommand
from .compact import CompactCommand
from .dream import DreamCommand
from .mode import ModeCommand
from .provider import ProviderCommand
from .session import SessionCommand
from .plugin import PluginCommand
from .skills import SkillsCommand

__all__ = [
    # Infrastructure
    "CommandResult",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandExecutor",
    "command",
    "resolve_selection",
    "parse_selection_arg",
    "register_builtin_commands",
    # Built-in commands
    "QuitCommand",
    "ClearCommand",
    "HelpCommand",
    "CompactCommand",
    "DreamCommand",
    "ModeCommand",
    "ProviderCommand",
    "SessionCommand",
    "PluginCommand",
    "SkillsCommand",
]

# Auto-registered built-in commands
BUILTIN_COMMANDS = [
    QuitCommand,
    ClearCommand,
    CompactCommand,
    HelpCommand,
    ModeCommand,
    ProviderCommand,
    SkillsCommand,
    SessionCommand,
    PluginCommand,
    DreamCommand,
]


def register_builtin_commands(registry):
    """Register all built-in commands"""
    for cmd_class in BUILTIN_COMMANDS:
        registry.register(cmd_class())

"""CLI commands - re-exports core infrastructure and registers CLI wrappers."""

from ...core.commands import (
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    CommandExecutor,
    command,
    resolve_selection,
    parse_selection_arg,
)
from .wrappers import (
    CLIQuitCommand as QuitCommand,
    CLIClearCommand as ClearCommand,
    CLIHelpCommand as HelpCommand,
    CLIModeCommand as ModeCommand,
    CLIProviderCommand as ProviderCommand,
    CLISessionCommand as SessionCommand,
    CLIDreamCommand as DreamCommand,
    CLIPluginCommand as PluginCommand,
    CLISkillsCommand as SkillsCommand,
    CLICompactCommand as CompactCommand,
)

__all__ = [
    "Command", "CommandContext", "CommandRegistry", "CommandResult",
    "CommandExecutor", "command",
    "QuitCommand", "ClearCommand", "HelpCommand", "CompactCommand",
    "ModeCommand", "ProviderCommand", "SkillsCommand",
    "SessionCommand", "PluginCommand", "DreamCommand",
]

BUILTIN_COMMANDS = [
    QuitCommand, ClearCommand, CompactCommand, HelpCommand,
    ModeCommand, ProviderCommand, SkillsCommand,
    SessionCommand, PluginCommand, DreamCommand,
]

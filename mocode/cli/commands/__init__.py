"""命令系统"""

from .base import Command, CommandContext, CommandRegistry, command
from .builtin import ClearCommand, HelpCommand, QuitCommand
from .executor import CommandExecutor
from .model import ModelCommand
from .plugin import PluginCommand
from .provider import ProviderCommand
from .session import SessionCommand
from .skills import SkillsCommand

__all__ = [
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandExecutor",
    "command",
    "register_builtin_commands",
    # 内置命令
    "QuitCommand",
    "ClearCommand",
    "HelpCommand",
    "ModelCommand",
    "ProviderCommand",
    "SkillsCommand",
    "SessionCommand",
    "PluginCommand",
]

# 自动注册的内置命令
BUILTIN_COMMANDS = [
    QuitCommand,
    ClearCommand,
    HelpCommand,
    ModelCommand,
    ProviderCommand,
    SkillsCommand,
    SessionCommand,
    PluginCommand,
]


def register_builtin_commands(registry):
    """注册所有内置命令"""
    for cmd_class in BUILTIN_COMMANDS:
        registry.register(cmd_class())

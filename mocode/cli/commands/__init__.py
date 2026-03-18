"""命令系统"""

from .base import Command, CommandContext, CommandRegistry, command
from .builtin import QuitCommand, ClearCommand, HelpCommand
from .model import ModelCommand
from .provider import ProviderCommand
from .skills_command import SkillsCommand
from .rtk import RtkCommand
from .session import SessionCommand

__all__ = [
    "Command",
    "CommandContext",
    "CommandRegistry",
    "command",
    "register_builtin_commands",
    # 内置命令
    "QuitCommand",
    "ClearCommand",
    "HelpCommand",
    "ModelCommand",
    "ProviderCommand",
    "SkillsCommand",
    "RtkCommand",
    "SessionCommand",
]

# 自动注册的内置命令
BUILTIN_COMMANDS = [
    QuitCommand,
    ClearCommand,
    HelpCommand,
    ModelCommand,
    ProviderCommand,
    SkillsCommand,
    RtkCommand,
    SessionCommand,
]


def register_builtin_commands(registry):
    """注册所有内置命令"""
    for cmd_class in BUILTIN_COMMANDS:
        registry.register(cmd_class())

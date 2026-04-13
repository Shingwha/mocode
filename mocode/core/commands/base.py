"""Command base classes - no UI dependencies"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, ClassVar, TypeVar, TYPE_CHECKING

from .result import CommandResult

if TYPE_CHECKING:
    from ..orchestrator import MocodeCore

T = TypeVar("T")


@dataclass
class CommandContext:
    """Command execution context - no UI dependency.

    The ``extra`` dict lets callers pass platform-specific context
    (e.g. a Display instance for CLI) without core depending on it.
    """

    client: "MocodeCore"
    args: str
    extra: dict = field(default_factory=dict)
    loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

    @property
    def config(self):
        return self.client.config

    @property
    def agent(self):
        return self.client.agent


class Command(ABC):
    """Command base class - returns CommandResult, no UI calls."""

    name: ClassVar[str] = ""
    aliases: ClassVar[list[str]] = []
    description: ClassVar[str] = ""

    @abstractmethod
    def execute(self, ctx: CommandContext) -> CommandResult:
        """Execute command. Returns CommandResult."""
        pass

    def match(self, cmd: str) -> bool:
        return cmd == self.name or cmd in self.aliases

    def _route_subcommand(
        self, ctx: CommandContext, arg: str, handlers: dict[str, str]
    ) -> CommandResult | None:
        """Route arg to subcommand method. Returns None if no match."""
        parts = arg.split(maxsplit=1)
        if not parts:
            return None
        subcmd, remaining = parts[0], parts[1] if len(parts) > 1 else ""
        if subcmd in handlers:
            return getattr(self, handlers[subcmd])(ctx, remaining)
        return None


class CommandRegistry:
    """Command registry (singleton)."""

    _instance = None
    _commands: dict[str, Command] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._commands = {}
        return cls._instance

    def register(self, cmd: Command):
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def unregister(self, name: str) -> bool:
        cmd = self._commands.get(name)
        if cmd is None:
            return False
        keys_to_remove = [name] + cmd.aliases
        for key in keys_to_remove:
            self._commands.pop(key, None)
        return True

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        seen = set()
        result = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return sorted(result, key=lambda c: c.name)

    def find_matches(self, prefix: str) -> list[Command]:
        seen = set()
        result = []
        for name, cmd in self._commands.items():
            if name.startswith(prefix) and cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return sorted(result, key=lambda c: c.name)


def command(name: str, *aliases, description: str = ""):
    """Command decorator."""
    def decorator(cls):
        cls.name = name
        cls.aliases = list(aliases)
        cls.description = description
        return cls
    return decorator

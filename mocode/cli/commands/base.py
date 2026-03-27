"""Command base class"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui.display import Display
    from ..ui import MenuAction
    from ...core.orchestrator import MocodeCore


@dataclass
class CommandContext:
    """Command execution context"""
    client: "MocodeCore"
    args: str  # Command arguments
    display: "Display | None" = None
    pending_message: str | None = None  # Message to send to agent after command

    @property
    def config(self):
        return self.client.config

    @property
    def agent(self):
        return self.client.agent


class Command(ABC):
    """Command base class"""

    name: ClassVar[str] = ""
    aliases: ClassVar[list[str]] = []
    description: ClassVar[str] = ""

    @abstractmethod
    def execute(self, ctx: CommandContext) -> bool:
        """Execute command. Returns True to continue, False to exit."""
        pass

    def match(self, cmd: str) -> bool:
        return cmd == self.name or cmd in self.aliases

    def confirm_delete(self, item_name: str) -> bool:
        from ..ui.prompt import confirm
        from ..ui.styles import YELLOW, RESET
        return confirm(f"{YELLOW}Delete '{item_name}'?{RESET}")


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

    def execute(self, ctx: CommandContext) -> bool:
        cmd = self.get(ctx.args.split()[0] if ctx.args else "")
        if cmd:
            parts = ctx.args.split(maxsplit=1)
            ctx.args = parts[1] if len(parts) > 1 else ""
            return cmd.execute(ctx)
        return True


def command(name: str, *aliases, description: str = ""):
    """Command decorator."""
    def decorator(cls):
        cls.name = name
        cls.aliases = list(aliases)
        cls.description = description
        return cls
    return decorator

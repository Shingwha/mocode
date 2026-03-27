"""Command base class"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, ClassVar, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui.display import Display
    from ..ui.prompt import MenuAction
    from ...core.orchestrator import MocodeCore

T = TypeVar("T")


@dataclass
class CommandContext:
    """Command execution context"""
    client: "MocodeCore"
    args: str  # Command arguments
    display: "Display | None" = None
    pending_message: str | None = None  # Message to send to agent after command
    loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

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

    # --- Safe output helpers ---

    def _info(self, ctx: CommandContext, msg: str) -> None:
        if ctx.display:
            ctx.display.info(msg)
        else:
            from ..ui.styles import info as _info
            _info(msg)

    def _success(self, ctx: CommandContext, msg: str) -> None:
        if ctx.display:
            ctx.display.success(msg)
        else:
            from ..ui.styles import success as _success
            _success(msg)

    def _error(self, ctx: CommandContext, msg: str) -> None:
        if ctx.display:
            ctx.display.error(msg)
        else:
            from ..ui.styles import error as _error
            _error(msg)

    def _output(self, ctx: CommandContext, msg: str) -> None:
        if ctx.display:
            ctx.display.command_output(msg)

    # --- Menu helpers ---

    def _select_from_list(
        self,
        title: str,
        items: list[T],
        formatter: Callable[[T], tuple[str, str]],
        *,
        extra_choices: list[tuple] | None = None,
        current: str | None = None,
    ) -> T | "MenuAction" | None:
        """Build select menu from items. Returns selected item, MenuAction, or None."""
        from ..ui.prompt import select, MenuItem, is_cancelled, MenuAction

        choices = [formatter(item) for item in items]
        if extra_choices:
            choices.extend(extra_choices)
        choices.append(MenuItem.exit_())

        result = select(title, choices, current=current)
        if is_cancelled(result):
            return None

        if isinstance(result, MenuAction):
            return result

        # Find original item by key
        for item in items:
            key, _ = formatter(item)
            if key == result:
                return item
        return None

    def _route_subcommand(
        self, ctx: CommandContext, arg: str, handlers: dict[str, str]
    ) -> bool | None:
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

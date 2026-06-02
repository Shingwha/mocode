"""Commands package — Command protocol, registry, and result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..app import CLIApp
    from ..display import Display


class CommandResult(Enum):
    CONTINUE = "continue"   # command handled, keep REPL running
    EXIT = "exit"           # break out of REPL


@dataclass
class CommandContext:
    app: CLIApp
    args: str               # everything after the command name, stripped
    display: Display


@runtime_checkable
class Command(Protocol):
    name: str                      # primary invoker, e.g. "/export"
    description: str               # shown in /help and autocomplete
    aliases: tuple[str, ...]       # e.g. ("/exit",) for /quit; ("quit","exit") bare words

    async def run(self, ctx: CommandContext) -> CommandResult: ...


class CommandRegistry:
    """Single source of truth for slash commands."""

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._by_alias: dict[str, Command] = {}
        self._order: list[Command] = []

    def register(self, cmd: Command) -> None:
        self._by_name[cmd.name] = cmd
        for a in cmd.aliases:
            self._by_alias[a] = cmd
        self._order.append(cmd)

    def get(self, text: str) -> Command | None:
        """Match by name or alias. Prefix-strips leading '/' for bare-word matching."""
        if text in self._by_name:
            return self._by_name[text]
        return self._by_alias.get(text)

    def all(self) -> list[Command]:
        """All registered commands, in registration order."""
        return list(self._order)

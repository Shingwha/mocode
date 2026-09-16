"""Slash commands — the Command dataclass, its registry, and result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from ..app import CLIApp
    from ..display import Display


class Kind(Enum):
    """What the REPL should do after a command finishes."""

    CONTINUE = "continue"
    EXIT = "exit"
    PROMPT = "prompt"


@dataclass(frozen=True)
class CommandResult:
    """Result of running a command. ``prompt`` is set only for ``Kind.PROMPT``."""

    kind: Kind
    prompt: str | None = None

    @classmethod
    def text(cls, text: str) -> CommandResult:
        """Send *text* to the agent without echoing it as a user message."""
        return cls(Kind.PROMPT, text)


CONTINUE = CommandResult(Kind.CONTINUE)
EXIT = CommandResult(Kind.EXIT)


@dataclass
class CommandContext:
    app: CLIApp
    args: str  # everything after the command name, stripped
    display: Display | None  # None in non-interactive (oneshot) mode


@dataclass
class Command:
    """A slash command. ``name`` must include the leading slash."""

    name: str
    description: str
    handler: Callable[[CommandContext], Awaitable[CommandResult]]
    aliases: tuple[str, ...] = ()


class CommandRegistry:
    """Single source of truth for slash commands."""

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._by_alias: dict[str, Command] = {}

    def register(self, *commands: Command) -> None:
        for cmd in commands:
            self._by_name[cmd.name] = cmd
            for alias in cmd.aliases:
                self._by_alias[alias] = cmd

    def get(self, text: str) -> Command | None:
        """Match by name or alias."""
        return self._by_name.get(text) or self._by_alias.get(text)

    def all(self) -> list[Command]:
        """All commands, sorted by name."""
        return sorted(self._by_name.values(), key=lambda c: c.name)

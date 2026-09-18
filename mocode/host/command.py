"""Commands — a named action a frontend can invoke.

A command is what a plugin contributes when it wants to be *called* rather than
*reasoned about*: ``/skill:foo`` is a command, ``read`` is a tool.

The contract here is frontend-agnostic. ``PROMPT`` ("send this text to the
agent") and ``EXIT`` ("end this interaction") mean the same thing to a web
backend as to a terminal, so an embedding application can dispatch the very same
commands the CLI does — including the ones a plugin contributed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .frontend import Frontend
    from .runtime import MoCode


class Kind(Enum):
    """What the frontend should do once a command finishes."""

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
    """What a handler is given.

    ``app`` is the runtime — agent, config, sessions, provider switching. It is
    deliberately not the frontend: a handler written against it works anywhere.

    ``frontend`` is whatever is showing the run, or ``None`` when headless. Use
    it for messages the user should see; a handler that needs more than the
    protocol offers (an interactive picker, say) belongs to that frontend rather
    than to the host.
    """

    app: MoCode
    args: str = ""
    frontend: Frontend | None = None
    commands: CommandRegistry | None = None

    def redraw(self) -> None:
        """Re-render after replacing the conversation. A no-op when headless."""
        if self.frontend is not None:
            self.frontend.conversation_changed(self.app.messages, self.app.tools)


@dataclass
class Command:
    """A named action. ``name`` must include the leading slash."""

    name: str
    description: str
    handler: Callable[[CommandContext], Awaitable[CommandResult]]
    aliases: tuple[str, ...] = ()


class CommandRegistry:
    """Single source of truth for commands."""

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


__all__ = [
    "CONTINUE",
    "EXIT",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "Kind",
]

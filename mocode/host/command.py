"""Commands — a named action a frontend can invoke.

A command is what a plugin contributes when it wants to be *called* rather than
*reasoned about*: ``/skill:foo`` is a command, ``read`` is a tool.

The contract here is frontend-agnostic. ``PROMPT`` ("send this text to the
agent") and ``EXIT`` ("end this interaction") mean the same thing to a web
backend as to a terminal, so every frontend can share one resolver instead of
each inventing its own idea of what a typed line means.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .conversation import Conversation


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

    ``conversation`` is the unit it acts on: its history, its project, its
    model, its session. A handler that has something to say does it by
    publishing an event — ``await ctx.conversation.notify("...")`` — so it works
    the same whether a terminal, a browser or nothing at all is watching.

    A handler that needs more than that (an interactive picker, the clipboard)
    belongs to the frontend that offers it, not to the host.
    """

    conversation: "Conversation"
    args: str = ""
    commands: "CommandRegistry | None" = None


@dataclass
class Command:
    """A named action. ``name`` must include the leading slash."""

    name: str
    description: str
    handler: Callable[[CommandContext], Awaitable[CommandResult]]
    aliases: tuple[str, ...] = ()


class CommandRegistry:
    """Single source of truth for the commands one frontend offers."""

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


async def dispatch(
    text: str, *, conversation: "Conversation", commands: CommandRegistry
) -> CommandResult:
    """Resolve one line of user input: a command if it names one, else a prompt.

    Shared by every frontend so ``/skill:release`` means the same thing in a
    terminal and in a browser. A line that merely starts with ``/`` and matches
    nothing is not a command — it comes back as ``PROMPT``, and the frontend
    decides what to say about it (the terminal suggests a spelling).
    """
    parts = text.split(None, 1)
    command = commands.get(parts[0].lower())
    if command is None:
        return CommandResult(Kind.PROMPT, text)
    ctx = CommandContext(
        conversation=conversation,
        args=parts[1] if len(parts) > 1 else "",
        commands=commands,
    )
    return await command.handler(ctx)


__all__ = [
    "CONTINUE",
    "EXIT",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "Kind",
    "dispatch",
]

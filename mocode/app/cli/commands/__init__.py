"""Commands package — Command dataclass, Subcommand, registry, and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from ..app import CLIApp
    from ..display import Display


# ── Result & Context ──────────────────────────────────────


@dataclass(frozen=True)
class CommandResult:
    """Result of running a command. Carries kind + optional prompt text."""

    kind: str  # "continue" | "exit" | "prompt"
    prompt: str | None  # populated only when kind == "prompt"

    @classmethod
    def text(cls, text: str) -> CommandResult:
        """Create a PROMPT result that sends text to the agent silently."""
        return cls(kind="prompt", prompt=text)


CommandResult.CONTINUE = CommandResult(kind="continue", prompt=None)
CommandResult.EXIT = CommandResult(kind="exit", prompt=None)


@dataclass
class CommandContext:
    app: CLIApp
    args: str  # everything after the command name, stripped
    display: Display


# ── Subcommand ────────────────────────────────────────────


@dataclass
class Subcommand:
    """Subcommand declaration — pure data descriptor."""
    name: str | tuple[str, ...]    # "run" or ("run", "r")
    description: str
    handler: Callable[[CommandContext, str], Awaitable[CommandResult]]
    aliases: tuple[str, ...] = ()


# ── Command ───────────────────────────────────────────────


@dataclass
class Command:
    """The only command type. Supports subcommands, leaf handlers, menu, and default."""
    name: str                                # "/workflow"
    description: str
    aliases: tuple[str, ...] = ()
    subcommands: tuple[Subcommand, ...] = () # non-empty when command has subcommands
    handler: Callable[[CommandContext], Awaitable[CommandResult]] | None = None
    menu: Callable[[CommandContext, Command], Awaitable[CommandResult]] | None = None
    default: Callable[[CommandContext, str], Awaitable[CommandResult]] | None = None

    async def run(self, ctx: CommandContext) -> CommandResult:
        """Entry dispatch: subcommand routing or leaf handler."""
        if not self.subcommands:
            # Leaf command
            if self.handler:
                return await self.handler(ctx)
            return CommandResult.CONTINUE

        # Has subcommands: parse first argument
        parts = ctx.args.split(None, 1) if ctx.args else []
        if parts:
            sub_name = parts[0].lower()
            remaining = parts[1] if len(parts) > 1 else ""
            sub = self._sub_lookup.get(sub_name)
            if sub:
                return await sub.handler(ctx, remaining)
            if self.default:
                return await self.default(ctx, ctx.args)
            available = ", ".join(
                s.name if isinstance(s.name, str) else s.name[0]
                for s in self.subcommands
            )
            ctx.display.warn(f"Unknown subcommand '{sub_name}'. Available: {available}")
            return CommandResult.CONTINUE

        # No arguments: menu or list subcommands
        if self.menu:
            return await self.menu(ctx, self)
        available = ", ".join(
            s.name if isinstance(s.name, str) else s.name[0]
            for s in self.subcommands
        )
        ctx.display.info(f"Available subcommands: {available}")
        return CommandResult.CONTINUE

    @cached_property
    def _sub_lookup(self) -> dict[str, Subcommand]:
        """name/alias → Subcommand lookup table, lazily built."""
        lookup: dict[str, Subcommand] = {}
        for sub in self.subcommands:
            names = (sub.name,) if isinstance(sub.name, str) else sub.name
            for n in names:
                lookup[n] = sub
            for a in sub.aliases:
                lookup[a] = sub
        return lookup


# ── CommandRegistry ───────────────────────────────────────


class CommandRegistry:
    """Single source of truth for slash commands."""

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._by_alias: dict[str, Command] = {}
        self._order: list[Command] = []

    def register(self, cmd: Command) -> None:
        """Register a command. Auto-expands subcommands as /prefix:subname."""
        # Register the main command
        self._by_name[cmd.name] = cmd
        for a in cmd.aliases:
            self._by_alias[a] = cmd
        self._order.append(cmd)

        # Auto-expand subcommands to colon-style entries
        if cmd.subcommands:
            prefix = cmd.name  # e.g. "/workflow"
            for sub in cmd.subcommands:
                primary = sub.name if isinstance(sub.name, str) else sub.name[0]
                member_name = f"{prefix}:{primary}"
                handler = sub.handler

                async def _leaf_run(ctx: CommandContext, _h=handler) -> CommandResult:
                    return await _h(ctx, ctx.args)

                member = Command(
                    name=member_name,
                    description=sub.description,
                    aliases=sub.aliases,
                    handler=_leaf_run,
                )
                self._by_name[member_name] = member
                self._order.append(member)
                for a in sub.aliases:
                    self._by_alias[a] = member

                # Also register secondary names from tuple (e.g. "ls" from ("list", "ls"))
                if isinstance(sub.name, tuple):
                    for secondary in sub.name[1:]:
                        alt_name = f"{prefix}:{secondary}"
                        self._by_alias[alt_name] = member

    def get(self, text: str) -> Command | None:
        """Match by name or alias."""
        if text in self._by_name:
            return self._by_name[text]
        return self._by_alias.get(text)

    def all(self) -> list[Command]:
        """All registered commands, sorted alphabetically by name."""
        return sorted(self._order, key=lambda c: c.name)

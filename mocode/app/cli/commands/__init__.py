"""Commands package — Command protocol, registry, and result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..app import CLIApp
    from ..display import Display


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


@runtime_checkable
class Command(Protocol):
    name: str  # primary invoker, e.g. "/export"
    description: str  # shown in /help and autocomplete
    aliases: tuple[str, ...]  # e.g. ("/exit",) for /quit; ("quit","exit") bare words

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
        """All registered commands, sorted alphabetically by name."""
        return sorted(self._order, key=lambda c: c.name)


# ── Subcommand group support ──────────────────────────────────


@dataclass
class Subcommand:
    """A handler definition inside a CommandGroup."""
    handler: Callable[[CommandContext, str], Awaitable[CommandResult]]
    name: str | tuple[str, ...] = ""        # "list" or ("list", "ls")
    description: str = ""
    aliases: tuple[str, ...] = ()


class CommandGroup:
    """A command that dispatches to subcommands. Satisfies Command protocol."""

    def __init__(
        self,
        name: str,
        description: str,
        subcmds: list[Subcommand],
        *,
        aliases: tuple[str, ...] = (),
        menu: Callable[[CommandContext, CommandGroup], Awaitable[CommandResult]] | None = None,
        default: Callable[[CommandContext, str], Awaitable[CommandResult]] | None = None,
    ):
        self.name = name
        self.description = description
        self.aliases = aliases
        self._menu = menu
        self._default = default
        # Build lookup: canonical_name → Subcommand
        #   e.g. {"list": sub, "ls": sub, "run": sub2, ...}
        self._subcmds: dict[str, Subcommand] = {}
        self._subcmd_list: list[Subcommand] = []   # unique, for display
        for sub in subcmds:
            self._subcmd_list.append(sub)
            names = (sub.name,) if isinstance(sub.name, str) else sub.name
            for n in names:
                self._subcmds[n] = sub
            for a in sub.aliases:
                self._subcmds[a] = sub

    async def run(self, ctx: CommandContext) -> CommandResult:
        return await self.dispatch(ctx, ctx.args)

    async def dispatch(self, ctx: CommandContext, args_str: str) -> CommandResult:
        parts = args_str.split(None, 1) if args_str else []
        if parts:
            sub_name = parts[0].lower()
            remaining = parts[1] if len(parts) > 1 else ""
            sub = self._subcmds.get(sub_name)
            if sub:
                return await sub.handler(ctx, remaining)
            # Unknown subcommand — try default handler (receives full args)
            if self._default:
                return await self._default(ctx, args_str)
            available = ", ".join(s.name if isinstance(s.name, str) else s.name[0]
                                  for s in self._subcmd_list)
            ctx.display.warn(f"Unknown subcommand '{sub_name}'. Available: {available}")
            return CommandResult.CONTINUE
        # No subcommand → interactive menu
        if self._menu:
            return await self._menu(ctx, self)
        available = ", ".join(s.name if isinstance(s.name, str) else s.name[0]
                              for s in self._subcmd_list)
        ctx.display.info(f"Available subcommands: {available}")
        return CommandResult.CONTINUE


def register_group(registry: CommandRegistry, group: CommandGroup, *, colon: bool = True) -> None:
    """Register a CommandGroup in the registry.

    The group itself is registered only in the lookup tables (so ``/workflow list``
    space-style dispatch works) but **not** in ``_order``, keeping it hidden from
    help output and autocomplete.  Colon-style entries are added to ``_order``
    so they appear in help/autocomplete.

    Args:
        registry: CommandRegistry
        group: CommandGroup instance
        colon: if True, also register each subcmd as /prefix:name
    """
    # Register group for name/alias lookup only (hidden from help/autocomplete)
    registry._by_name[group.name] = group
    for a in group.aliases:
        registry._by_alias[a] = group

    if not colon:
        return
    prefix = group.name  # e.g. "/plan"
    for sub in group._subcmd_list:
        primary = sub.name if isinstance(sub.name, str) else sub.name[0]
        cmd_name = f"{prefix}:{primary}"
        member = _make_member(cmd_name, sub.description, sub.aliases, sub.handler)
        registry.register(member)


def _make_member(
    name: str,
    description: str,
    aliases: tuple[str, ...],
    handler: Callable[[CommandContext, str], Awaitable[CommandResult]],
):
    """Create a Command from a Subcommand handler (for colon-style registration)."""
    async def _run(ctx: CommandContext) -> CommandResult:
        return await handler(ctx, ctx.args)
    _run.__qualname__ = f"GroupMember({name})"
    return type("GroupMember", (), {
        "name": name,
        "description": description,
        "aliases": aliases,
        "run": _run,
    })()

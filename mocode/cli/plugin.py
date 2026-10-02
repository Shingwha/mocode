"""The terminal's plugin interface — contributing to the terminal, not the agent.

Two surfaces, and the difference is who can use the result:

* A **host plugin** (``<plugin>/mocode/plugin.py``, ``Plugin.build(ctx)``)
  contributes to the agent: tools, prompt sections, hooks, skills, and commands
  that work in any frontend because they only need a conversation.
* A **terminal plugin** (``<plugin>/mocode.cli/plugin.py``, or a
  ``mocode.cli/plugin/`` package, :meth:`CLIPlugin.build`) contributes to this
  application: chrome that only a terminal can honour — a command with a
  picker, a rendering for its own events, a line while it works.

Both live in the same plugin directory, in different namespaces, and each side
ignores the other's — which is the Agent Plugins rule, and the reason a plugin
written for the terminal still travels to a web frontend that simply does not
read ``mocode.cli``.

A terminal plugin is built against a :class:`CLIContext`, never the
application: the context carries the three things a plugin may contribute
through (commands, drawers, the runtime UI channel) and nothing it should not
touch. That narrowness is the contract — the internals it keeps out are the
ones a TUI rewrite needs freedom in.

The terminal's own commands are the first implementation of this interface
(:class:`BuiltinCommands`), so there is one way to contribute here, not two.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from ..core.events import Notice, PluginMessage
from ..host.plugin.loader import (
    code_entry,
    import_module_file,
    report,
    resolve_plugin,
    slugify,
)

if TYPE_CHECKING:
    from ..core.agent import Turn
    from ..core.events import Event
    from ..core.provider import Usage
    from ..host.command import CommandRegistry
    from ..host.conversation import Conversation
    from . import lines as L
    from .theme import Theme

_log = logging.getLogger(__name__)

#: The directory a plugin uses to contribute to this frontend.
NAMESPACE = "mocode.cli"

#: What a drawer is: an event in, the lines it should draw as out.
Drawer = Callable[["Event"], list["L.Line"]]


class DrawerRegistry:
    """Event kind → the lines it draws as.

    The one way anything shapes how a message-like event renders: register a
    drawer for the event's *type*, or — for a plugin message — for its
    ``kind`` string. The built-in events are pre-registered here through the
    same table (there is no second path), and an event with no drawer falls
    back to its own ``summary()`` — which is why a plugin's custom event
    already draws, registered or not, in any frontend written before it.

    Registering over an existing key needs ``override=True``: replacing a
    built-in (re-skinning the notices, say) should be a deliberate act, not an
    accident of load order.
    """

    def __init__(self) -> None:
        self._by_type: dict[type, Drawer] = {}
        self._by_kind: dict[str, Drawer] = {}
        from . import lines as L

        self.register(Notice, lambda e: [L.notice(e.message, e.level)])
        # The kind ``ui.message`` speaks, reserved for message events riding
        # the conversation: text in, one line out.
        self.register("text", lambda e: [L.notice(str(e.data.get("text", "")), "info")])

    def register(self, key: "type | str", fn: Drawer, *, override: bool = False) -> None:
        """Register *fn* for an event type, or for a plugin-message kind."""
        table = self._by_kind if isinstance(key, str) else self._by_type
        if key in table and not override:
            raise ValueError(
                f"a drawer is already registered for {key!r}; "
                "pass override=True to replace it"
            )
        table[key] = fn

    def lines_for(self, event: "Event") -> "list[L.Line] | None":
        """The drawer's lines for *event*, or ``None`` when no drawer matches."""
        drawer = self._by_type.get(type(event))
        if drawer is None and isinstance(event, PluginMessage) and event.kind:
            drawer = self._by_kind.get(event.kind)
        return drawer(event) if drawer is not None else None


class UI:
    """The runtime channel — what a plugin may ask of whoever is watching.

    The skeleton of the surface the interactive line grows: ``is_interactive``
    is the switch every degradation hangs off (a pipe has no one to ask), and
    ``message`` is the one thing that works everywhere. Dialogs — confirm,
    select — arrive with the interactive work and are deliberately absent
    here rather than stubbed.
    """

    def __init__(self, conversation: "Conversation", *, is_interactive: bool):
        self._conversation = conversation
        #: Whether this frontend can ask the user something mid-run. A plugin
        #: decides its fallbacks on this; a pipe answers no.
        self.is_interactive = is_interactive

    async def message(self, text: str) -> None:
        """One line for whoever is watching — said on the stream, not printed.

        Rides the conversation's own channel, so it reaches every frontend and
        the data stays with the host — the same split as every other line:
        rendering belongs to the frontend that has a screen. When message-kind
        plugin events land on the conversation, this becomes their built-in
        drawer without the signature changing.
        """
        await self._conversation.notify(text)


# ── Keys ─────────────────────────────────────────────────


@dataclass
class KeyContext:
    """What a key handler sees: the conversation, the UI channel, and whichever
    editing surface is live.

    While *idle*, *buffer* is the prompt_toolkit buffer being edited and
    *turn* is ``None``; while *running*, *turn* is the in-flight turn and
    *buffer* is ``None``. Handlers may be sync or async (async is awaited on
    the running path; idle handlers run inside prompt_toolkit and stay sync).
    """

    conversation: "Conversation"
    ui: UI
    buffer: object | None = None
    turn: "Turn | None" = None

    @property
    def buffer_text(self) -> str:
        """The input being edited, or ``""`` while a turn is running."""
        buffer = self.buffer
        return getattr(buffer, "text", "") if buffer is not None else ""


@dataclass
class KeyBinding:
    """One registered key: which key, when it fires, what it does."""

    key: str
    handler: Callable[[KeyContext], object]
    when: str  # "idle" | "running"
    description: str = ""


class KeyRegistry:
    """Keys a plugin (or the terminal itself) wants answered.

    ``when="idle"`` bindings join the edit-time PromptSession keybindings the
    next time one is built — registrations made after the first prompt take
    effect when the session is rebuilt (a new conversation, a new app).
    ``when="running"`` bindings are read by the raw key loop the app runs
    while a turn is in flight.
    """

    def __init__(self) -> None:
        self._bindings: list[KeyBinding] = []

    def add(
        self,
        key: str,
        handler: Callable[[KeyContext], object],
        *,
        when: str = "idle",
        description: str = "",
    ) -> None:
        """Register *handler* for *key* (prompt_toolkit names: ``"c-g"``,
        ``"escape"``, …). *when* is ``"idle"`` or ``"running"``."""
        if when not in ("idle", "running"):
            raise ValueError(f"when must be 'idle' or 'running', not {when!r}")
        self._bindings.append(KeyBinding(key, handler, when, description))

    def idle(self) -> list[KeyBinding]:
        """The edit-time bindings, in registration order."""
        return [b for b in self._bindings if b.when == "idle"]

    def running(self, key: str) -> KeyBinding | None:
        """The running-time binding for *key*, if one was registered."""
        for b in self._bindings:
            if b.when == "running" and b.key == key:
                return b
        return None


# ── Input middleware ─────────────────────────────────────


class InputMiddleware:
    """The ``text -> text`` chain input passes through before dispatch.

    Each middleware sees the submitted line and returns what the next one
    should see; returning ``None`` consumes the line — it is neither
    dispatched as a command nor sent to the model, and the prompt simply
    comes back. Registration order is run order.
    """

    def __init__(self) -> None:
        self._chain: list[Callable[[str], "str | None"]] = []

    def use(self, fn: Callable[[str], "str | None"]) -> None:
        """Append *fn* to the chain."""
        self._chain.append(fn)

    def run(self, text: str) -> "str | None":
        """Fold *text* through the chain; ``None`` means consumed."""
        for fn in self._chain:
            text = fn(text)
            if text is None:
                return None
        return text


# ── Status bar ───────────────────────────────────────────


@dataclass
class StatusState:
    """What the status bar knows about the world — rebuilt on every redraw."""

    model: str
    cwd: Path
    running: bool = False
    usage: "Usage | None" = None
    pending_approvals: int = 0


@dataclass
class Segment:
    """One piece of the status bar. Higher *priority* sorts further left."""

    text: str
    priority: int = 0


class StatusRegistry:
    """The bottom bar as a merge of equal citizens, not a single owner.

    Every redraw runs all registered providers against the current
    :class:`StatusState`, keeps the non-``None`` segments, sorts them by
    priority (highest leftmost), joins with ``" · "`` and truncates on the
    right at the terminal width. A provider that has nothing to say says
    ``None`` and leaves its slot empty.
    """

    SEPARATOR = " · "

    def __init__(self, state_fn: Callable[[], StatusState] | None = None):
        self._providers: list[Callable[[StatusState], Segment | None]] = []
        #: How the current state is obtained; the app injects this.
        self._state_fn = state_fn

    def use(self, fn: Callable[[StatusState], "Segment | None"]) -> None:
        """Contribute one segment; ``None`` means nothing to show right now."""
        self._providers.append(fn)

    def render(self, state: StatusState, width: int) -> str:
        """Merge every provider's segment for *state* into one line."""
        segments = [
            s for fn in self._providers if (s := fn(state)) is not None and s.text
        ]
        segments.sort(key=lambda s: -s.priority)
        line = ""
        for segment in segments:
            piece = segment.text if not line else self.SEPARATOR + segment.text
            if len(line) + len(piece) > width:
                break
            line += piece
        if not line and segments:
            # Even the highest-priority segment alone is too wide: show what fits.
            line = segments[0].text[: max(width - 1, 0)] + "…" if width > 1 else ""
        return line

    def toolbar(self) -> str:
        """The current bar as one line, for prompt_toolkit's ``bottom_toolbar``."""
        if self._state_fn is None:
            return ""
        from shutil import get_terminal_size

        return self.render(self._state_fn(), get_terminal_size().columns)


# ── Header ───────────────────────────────────────────────


class HeaderRegistry:
    """Lines printed above the prompt when set — print-style decoration.

    There is no live header region (no Application, by decision): setting the
    header prints the lines into the scroll-back above the prompt, once. The
    sink is injected by the app; without one (a pipe) setting is remembered
    but never printed.
    """

    def __init__(self, sink: Callable[[str], None] | None = None):
        self._sink = sink
        self._lines: list[str] = []

    def bind(self, sink: Callable[[str], None] | None) -> None:
        """Where printed lines go; the app injects its printer at assembly."""
        self._sink = sink

    def set(self, lines: list[str]) -> None:
        """Print *lines* above the prompt; an empty list clears it."""
        self._lines = list(lines)
        if self._sink is not None:
            for line in self._lines:
                self._sink(line)

    @property
    def lines(self) -> list[str]:
        return list(self._lines)


@dataclass
class CLIContext:
    """What :meth:`CLIPlugin.build` receives — the terminal plugin API, whole.

    The surface a plugin may contribute through: commands, drawers, keys,
    input middleware, status and header — plus the read-only views a plugin
    needs to know where it is (the theme, the conversation) and
    :meth:`on_close` for releasing what it built. The internals it keeps out —
    the app, the display, the input session, the renderer — are the ones a
    TUI rewrite needs freedom in; a plugin written against this object
    survives one. There is deliberately no ``ctx.app`` / ``ctx.display`` /
    ``ctx.renderer``: a plugin that needs a lower ability gets it by that
    ability being promoted into this context, not by a hole.
    """

    commands: "CommandRegistry"
    drawers: DrawerRegistry
    ui: UI
    keys: KeyRegistry
    input: InputMiddleware
    status: StatusRegistry
    header: HeaderRegistry
    #: The terminal's appearance, read-only. Style *registration* (a plugin
    #: adding its own style names) is deliberately not v1 — the frozen
    #: dataclass is the whole theme, and re-skinning means replacing it.
    theme: "Theme | None"
    conversation: "Conversation"
    _close_callbacks: list[Callable[[], object]] = field(default_factory=list)

    def on_close(self, fn: Callable[[], object]) -> None:
        """Register *fn* to run when this terminal goes away — release a
        resource the plugin opened. Callbacks run in reverse registration
        order, each in isolation: one failing callback costs itself, never
        the others. May be sync or async."""
        self._close_callbacks.append(fn)

    async def aclose(self) -> None:
        """Run every registered close callback, last-registered first."""
        callbacks, self._close_callbacks = self._close_callbacks, []
        for fn in reversed(callbacks):
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _log.exception("a CLI plugin's on_close callback failed")


class CLIPlugin:
    """A contribution to the terminal, rather than to the agent."""

    name: str = ""
    description: str = ""

    def build(self, ctx: CLIContext) -> None:
        """Contribute to the terminal. Default: contribute nothing."""


class BuiltinCommands(CLIPlugin):
    """The terminal's own commands — the ones that need a terminal."""

    name = "cli"
    description = "Terminal commands: /quit /copy /model /resume"

    def build(self, ctx: CLIContext) -> None:
        # Imported here so a headless run never pays for questionary.
        from .commands import COMMANDS

        ctx.commands.register(*COMMANDS)


def load_cli_plugins(sources: Iterable[Path]) -> list[CLIPlugin]:
    """The terminal plugins among an installed plugin set.

    *sources* are the plugin directories loaded for a project (``MoCode``
    exposes them as ``plugin_sources_for``); each one is asked for its
    ``mocode.cli`` namespace and nothing else — with the same entry judgment
    the host applies to ``mocode/``: ``plugin.py`` or a ``plugin/`` package,
    and a near-miss reported rather than skipped. A plugin that cannot be
    imported is reported and skipped.
    """
    found: list[CLIPlugin] = []
    for source in sources:
        entry = code_entry(Path(source) / NAMESPACE)
        if entry is None:
            continue
        module = import_module_file(
            entry,
            f"mocode_cli_plugin_{slugify(source.name)}",
            fix=f"mocode plugin sync {source.name}",
        )
        if module is None:
            continue
        plugin = resolve_plugin(module, CLIPlugin, fallback_name=source.name)
        if isinstance(plugin, CLIPlugin):
            if not plugin.name:
                plugin.name = source.name
            found.append(plugin)
    return found


def build_cli_plugins(ctx: CLIContext, sources: Iterable[Path]) -> list[CLIPlugin]:
    """Load and build every terminal plugin a project installed."""
    plugins: list[CLIPlugin] = [BuiltinCommands(), *load_cli_plugins(sources)]
    for plugin in plugins:
        try:
            plugin.build(ctx)
        except Exception as e:  # one broken plugin must not cost the screen
            report(f"{plugin.name}: build() failed: {e}")
    return plugins


__all__ = [
    "NAMESPACE",
    "BuiltinCommands",
    "CLIContext",
    "CLIPlugin",
    "Drawer",
    "DrawerRegistry",
    "HeaderRegistry",
    "InputMiddleware",
    "KeyBinding",
    "KeyContext",
    "KeyRegistry",
    "Segment",
    "StatusRegistry",
    "StatusState",
    "UI",
    "build_cli_plugins",
    "load_cli_plugins",
]

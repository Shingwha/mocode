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

from dataclasses import dataclass
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
    from ..core.events import Event
    from ..host.command import CommandRegistry
    from ..host.conversation import Conversation
    from . import lines as L

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


@dataclass
class CLIContext:
    """What :meth:`CLIPlugin.build` receives — the terminal plugin API, whole.

    Three members, deliberately: the commands registry, the drawer table for
    shaping how message-like events render, and the runtime UI channel. The
    internals it keeps out — the app, the display, the input session — are the
    ones a TUI rewrite needs freedom in; a plugin written against this object
    survives one.
    """

    commands: "CommandRegistry"
    drawers: DrawerRegistry
    ui: UI


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
    "UI",
    "build_cli_plugins",
    "load_cli_plugins",
]

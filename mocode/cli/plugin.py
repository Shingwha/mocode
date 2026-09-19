"""The terminal's plugin interface — contributing to the terminal, not the agent.

Two surfaces, and the difference is who can use the result:

* A **host plugin** (``<plugin>/mocode/plugin.py``, ``Plugin.build(ctx)``)
  contributes to the agent: tools, prompt sections, hooks, skills, and commands
  that work in any frontend because they only need a conversation.
* A **terminal plugin** (``<plugin>/mocode.cli/plugin.py``, :meth:`CLIPlugin.build`)
  contributes to this application: chrome that only a terminal can honour — a
  command with a picker, a keybinding, a screen. It is built against the CLI
  itself, so it can reach ``cli.commands``, ``cli.display``, ``cli.input`` and
  ``cli.conversation``.

Both live in the same plugin directory, in different namespaces, and each side
ignores the other's — which is the Agent Plugins rule, and the reason a plugin
written for the terminal still travels to a web frontend that simply does not
read ``mocode.cli``.

The terminal's own commands are the first implementation of this interface
(:data:`PLUGIN`), so there is one way to contribute here, not two.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from ..host.plugin.loader import import_module_file, resolve_plugin

if TYPE_CHECKING:
    from .app import CLIApp

#: The directory a plugin uses to contribute to this frontend.
NAMESPACE = "mocode.cli"

#: The module inside it.
ENTRY = "plugin.py"


class CLIPlugin:
    """A contribution to the terminal, rather than to the agent."""

    name: str = ""
    description: str = ""

    def build(self, cli: "CLIApp") -> None:
        """Contribute to the terminal. Default: contribute nothing."""


class BuiltinCommands(CLIPlugin):
    """The terminal's own slash commands."""

    name = "cli"
    description = "Terminal commands: /help /clear /copy /model /export /resume"

    def build(self, cli: "CLIApp") -> None:
        # Imported here so a headless run never pays for questionary.
        from .commands import COMMANDS

        cli.commands.register(*COMMANDS)


PLUGIN = BuiltinCommands()


def load_cli_plugins(sources: Iterable[Path]) -> list[CLIPlugin]:
    """The terminal plugins among an installed plugin set.

    *sources* are the plugin directories loaded for a project (``MoCode``
    exposes them as ``plugin_sources_for``); each one is asked for its
    ``mocode.cli`` namespace and nothing else. A plugin that cannot be imported
    is reported and skipped.
    """
    found: list[CLIPlugin] = []
    for source in sources:
        entry = Path(source) / NAMESPACE / ENTRY
        if not entry.is_file():
            continue
        module = import_module_file(entry, f"mocode_cli_plugin_{_slug(source.name)}")
        if module is None:
            continue
        plugin = resolve_plugin(module, CLIPlugin, fallback_name=source.name)
        if isinstance(plugin, CLIPlugin):
            if not plugin.name:
                plugin.name = source.name
            found.append(plugin)
    return found


def build_cli_plugins(cli: "CLIApp", sources: Iterable[Path]) -> list[CLIPlugin]:
    """Load and build every terminal plugin a project installed."""
    plugins = [PLUGIN, *load_cli_plugins(sources)]
    for plugin in plugins:
        try:
            plugin.build(cli)
        except Exception as e:  # one broken plugin must not cost the screen
            print(f"[plugin] {plugin.name}: build() failed: {e}", file=sys.stderr)
    return plugins


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name)


__all__ = [
    "ENTRY",
    "NAMESPACE",
    "PLUGIN",
    "BuiltinCommands",
    "CLIPlugin",
    "build_cli_plugins",
    "load_cli_plugins",
]

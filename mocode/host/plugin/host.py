"""PluginHost — loads plugins for a project, builds them for a conversation.

The two halves are deliberately separate, and they belong to different owners:

* :func:`load_plugins` — the disk scan and the module imports, for one working
  directory. A directory's plugin set is the same for every conversation in it,
  so a runtime loads it once and caches it.
* :class:`PluginHost` — ``build()`` for one conversation. Everything a plugin
  creates that is *stateful* (a shell session, a skill index, a tool with a
  cursor) is created here, and that is what keeps two conversations in the same
  process from sharing anything.

The invariant that makes the split sound: **a plugin instance is stateless.**
Contributions are made in ``build(ctx)``; a plugin that keeps conversation state
on ``self`` leaks it into the next conversation, and nothing here can stop it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from ...core.agent import AgentConfig, AgentLoop
from ...core.hook import HookRunner
from ...core.provider import Provider
from ..prompt import build_system_prompt
from .base import Plugin
from .context import HostContext
from .loader import discover, load_plugin, report

if TYPE_CHECKING:
    from ..config import Config


@dataclass
class LoadedPlugins:
    """What one project's plugin directories yielded.

    ``sources`` is the directories the plugins live in, which a frontend needs
    to find its own namespace inside them (``<source>/mocode.cli/``) and which
    a plugin uses to reach the files it ships (``<source>/skills/``). The host
    hands the paths over without reading them.
    """

    plugins: list[Plugin] = field(default_factory=list)
    sources: list[Path] = field(default_factory=list)


def builtin_plugins() -> list[Plugin]:
    """The plugins MoCode ships, in a fixed (prompt-stable) order."""
    from .builtin import default_prompts, filesystem, help, session, shell, skills

    return [
        filesystem.PLUGIN,
        shell.PLUGIN,
        skills.PLUGIN,
        default_prompts.PLUGIN,
        session.PLUGIN,
        help.PLUGIN,
    ]


def default_plugin_dirs(cwd: Path, home: Path) -> list[Path]:
    """Where a project's plugins are looked for, most specific first."""
    return [cwd / ".mocode" / "plugins", home / "plugins"]


def load_plugins(
    *, plugin_dirs: Sequence[Path], config: "Config", reserved: Sequence[str] = ()
) -> LoadedPlugins:
    """Discover, import and filter the plugins belonging to *plugin_dirs*.

    Discovery never imports anything it is not about to use, and a plugin that
    cannot be imported is reported on stderr and skipped — one broken plugin
    must not take the host down. Names in *reserved* cannot be shadowed.

    A plugin with no code is not a mistake: ``skills/`` alone is a plugin.
    """
    builtins = builtin_plugins()
    blocked = {p.name for p in builtins} | set(reserved)

    loaded = LoadedPlugins()
    for plugin in builtins:
        if _enabled(config, plugin.name, True):
            loaded.plugins.append(plugin)

    for spec in discover(list(plugin_dirs), reserved=blocked):
        if not _enabled(config, spec.name, True):
            continue
        if spec.directory is not None:
            loaded.sources.append(spec.directory)
        plugin = load_plugin(spec)
        if plugin is not None:
            loaded.plugins.append(plugin)
    return loaded


def _enabled(config: "Config", name: str, default: bool) -> bool:
    configured = (config.plugins.get(name) or {}).get("enabled") if config else None
    return default if configured is None else bool(configured)


class PluginHost:
    """Builds one conversation's contributions from already-loaded plugins."""

    def __init__(self, ctx: HostContext, plugins: Sequence[Plugin]):
        self.ctx = ctx
        self.plugins = list(plugins)
        #: Names of plugins whose build() or close() failed — the rest still worked.
        self.failures: list[str] = []
        self._closed = False

    def build_all(self) -> None:
        """Run every plugin's build(). One failure never stops the host."""
        for plugin in self.plugins:
            try:
                plugin.build(self.ctx)
            except Exception as e:
                self.failures.append(plugin.name)
                report(f"{plugin.name}: build() failed: {e}")

    def assemble(self, *, provider: Provider, config: AgentConfig) -> AgentLoop:
        """Build the system prompt from current contributions and wire the agent."""
        agent = AgentLoop(
            provider=provider,
            system_prompt=build_system_prompt(self.ctx),
            tools=self.ctx.tools,
            hooks=HookRunner(self.ctx.hooks),
            config=config,
            model=self.ctx.model,
        )
        self.ctx.agent = agent
        return agent

    def rebuild_prompt(self) -> None:
        """Re-render the system prompt from the contributions as they stand now."""
        if self.ctx.agent is not None:
            self.ctx.agent.system_prompt = build_system_prompt(self.ctx)

    def run(self, *, provider: Provider, config: AgentConfig) -> AgentLoop:
        """Build every contribution, then assemble the agent."""
        self.build_all()
        return self.assemble(provider=provider, config=config)

    def close(self) -> None:
        """Tell every plugin this conversation is over. Idempotent."""
        if self._closed:
            return
        self._closed = True
        for plugin in self.plugins:
            try:
                plugin.close(self.ctx)
            except Exception as e:
                self.failures.append(plugin.name)
                report(f"{plugin.name}: close() failed: {e}")


__all__ = [
    "LoadedPlugins",
    "PluginHost",
    "builtin_plugins",
    "default_plugin_dirs",
    "load_plugins",
]

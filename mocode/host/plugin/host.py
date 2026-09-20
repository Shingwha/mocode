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
    from ..session import Session


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
    from .builtin import (
        cache_protect,
        default_prompts,
        filesystem,
        help,
        session,
        shell,
        skills,
    )

    return [
        filesystem.PLUGIN,
        shell.PLUGIN,
        skills.PLUGIN,
        default_prompts.PLUGIN,
        session.PLUGIN,
        help.PLUGIN,
        cache_protect.PLUGIN,
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
    """Builds one conversation's contributions from already-loaded plugins.

    It is also the single owner of that conversation's **request surface** —
    the system prompt and the offered tool interface. History is data and is
    adopted eagerly; the surface is derived state, written in exactly one
    place (``_install``) and materialized exactly once (:meth:`materialize`):
    from the session a resume carries, or freshly — async preparation, then
    render, then freeze — before the first request goes out.
    """

    def __init__(
        self, ctx: HostContext, plugins: Sequence[Plugin], *, freeze: bool = True
    ) -> None:
        self.ctx = ctx
        self.plugins = list(plugins)
        #: Names of plugins whose build()/prepare()/close() failed — the rest
        #: still worked.
        self.failures: list[str] = []
        #: Whether the offered tool interface is held still once materialized
        #: (the runtime's ``freeze_interface`` decision, passed down here).
        self._freeze = freeze
        self._closed = False
        self._materialized = False
        self._pending_session: "Session | None" = None

    def build_all(self) -> None:
        """Run every plugin's build(). One failure never stops the host."""
        for plugin in self.plugins:
            try:
                plugin.build(self.ctx)
            except Exception as e:
                self.failures.append(plugin.name)
                report(f"{plugin.name}: build() failed: {e}")

    async def prepare_all(self) -> None:
        """Run every plugin's prepare(). One failure never stops the host."""
        for plugin in self.plugins:
            try:
                await plugin.prepare(self.ctx)
            except Exception as e:
                self.failures.append(plugin.name)
                report(f"{plugin.name}: prepare() failed: {e}")

    def adopt_session(self, session: "Session") -> None:
        """Hand over the surface a resumed conversation should run on.

        Consumed by :meth:`materialize`; a session with nothing stored (never
        ran, or recorded before surfaces were frozen) materializes freshly.
        """
        self._pending_session = session

    async def materialize(self) -> None:
        """Write the request surface, exactly once — the first caller wins.

        A resumed conversation gets its recorded surface back byte-identical,
        so the provider's prefix cache survives. Anything else runs the
        plugins' async preparation, then renders the prompt and freezes the
        interface from the contributions as they now stand — which is why
        ``build()`` must stay cheap and leave the I/O to ``prepare()``.
        """
        if self._materialized:
            return
        self._materialized = True
        session = self._pending_session
        if session is not None and session.system_prompt:
            self.reinstate(session)
            return
        await self.prepare_all()
        self._install(build_system_prompt(self.ctx), None)

    def reinstate(self, session: "Session") -> None:
        """Adopt the surface a stored session ran on, byte-identical.

        A session recorded with no surface is left alone — the conversation
        materializes freshly at its first request instead.
        """
        if not session.system_prompt:
            return
        self._install(session.system_prompt, session.tool_schemas or None)
        self._materialized = True

    def _install(self, prompt: str, schemas: "list[dict] | None") -> None:
        """The one place the request surface is written."""
        if self.ctx.agent is not None:
            self.ctx.agent.system_prompt = prompt
        if self._freeze:
            if schemas is not None:
                self.ctx.tools.freeze(schemas)
            else:
                self.ctx.tools.freeze()

    def rebuild(self) -> None:
        """Deliberately re-materialize: re-render, re-pin, forget baselines.

        The explicit escape hatch — a resume keeps the surface and announces
        drift as notices instead; this replaces it outright, accepting the
        cache loss. Runs no preparation: it re-reads what is registered.
        """
        if self.ctx.agent is not None:
            self.ctx.agent.system_prompt = build_system_prompt(self.ctx)
        if self._freeze:
            self.ctx.tools.freeze()
        self.ctx.plugin_states.clear()
        self._materialized = True

    def assemble(self, *, provider: Provider, config: AgentConfig) -> AgentLoop:
        """Wire the agent. The surface is not written here — the loop awaits
        :meth:`materialize` at the top of every turn, before it captures its
        baseline of the prompt."""
        agent = AgentLoop(
            provider=provider,
            system_prompt="",  # materialized before the first request
            tools=self.ctx.tools,
            hooks=HookRunner(self.ctx.hooks),
            config=config,
            model=self.ctx.model,
            prepare=self.materialize,
        )
        self.ctx.agent = agent
        return agent

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

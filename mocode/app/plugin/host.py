"""PluginHost — loads plugins, builds them, and assembles the agent.

This replaces the old hand-written ``_build_agent``: the host knows nothing
about individual tools or commands, it only runs plugin contributions and wires
the result into an AgentLoop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ...core.agent import AgentConfig, AgentLoop
from ...core.hook import HookRunner
from ...core.provider import Provider
from .base import Plugin
from .context import HostContext
from .loader import discover, load_plugin


def builtin_plugins() -> list[Plugin]:
    """The plugins shipped with MoCode, in a fixed (prompt-stable) order."""
    from .builtin import cli, filesystem, shell, skills

    return [filesystem.PLUGIN, shell.PLUGIN, skills.PLUGIN, cli.PLUGIN]


class PluginHost:
    """Owns the plugin lifecycle: discover → build → assemble."""

    def __init__(self, ctx: HostContext, *, plugin_dirs: list[Path] | None = None):
        self.ctx = ctx
        self.plugin_dirs = (
            list(plugin_dirs)
            if plugin_dirs is not None
            else [ctx.cwd / ".mocode" / "plugins", ctx.home / "plugins"]
        )
        self.plugins: list[Plugin] = []
        self.failures: list[str] = []

    def load(self) -> list[Plugin]:
        """Discover built-in and directory plugins, honouring enable/disable."""
        builtins = builtin_plugins()
        reserved = {p.name for p in builtins}
        self.plugins = [p for p in builtins if self._enabled(p.name, True)]

        for spec in discover(self.plugin_dirs, reserved=reserved):
            if not self._enabled(spec.name, spec.enabled):
                continue
            plugin = load_plugin(spec)
            if plugin is None:
                self.failures.append(spec.name)
                continue
            self.plugins.append(plugin)
        return self.plugins

    def build_all(self) -> None:
        """Run every plugin's build(). One failure never stops the host."""
        for plugin in self.plugins:
            try:
                plugin.build(self.ctx)
            except Exception as e:
                self.failures.append(plugin.name)
                print(
                    f"[plugin] {plugin.name}: build() failed: {e}",
                    file=sys.stderr,
                )

    def assemble(self, *, provider: Provider, config: AgentConfig) -> AgentLoop:
        """Build the system prompt from current contributions and wire the agent."""
        from ..prompt import build_system_prompt

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

    def run(self, *, provider: Provider, config: AgentConfig) -> AgentLoop:
        """Load, build and assemble in one call."""
        self.load()
        self.build_all()
        return self.assemble(provider=provider, config=config)

    def _enabled(self, name: str, default: bool) -> bool:
        configured = self.ctx.plugin_config(name).get("enabled")
        return default if configured is None else bool(configured)

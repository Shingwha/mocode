"""The plugin framework — discovery, loading, isolation, assembly."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mocode.host.config import Config
from mocode.host.plugin.base import Plugin
from mocode.host.plugin.context import HostContext
from mocode.host.plugin.host import PluginHost, builtin_plugins
from mocode.host.plugin.loader import discover, load_plugin, parse_frontmatter
from mocode.core.agent import AgentConfig
from mocode.core.provider import ModelSpec

from .providers import MockProvider

GREET_CODE = """
    from mocode.plugins import Plugin, Tool

    class GreetTool(Tool):
        def __init__(self):
            super().__init__(
                name="greet", description="Greet", params={},
                func=lambda args: "hi", tags=frozenset({"demo"}),
            )

    class GreetPlugin(Plugin):
        name = "greet"
        description = "greets"

        def build(self, ctx):
            ctx.tools.register(GreetTool())
"""

COMMAND_CODE = """
    from mocode.plugins import CONTINUE, Command, Plugin

    async def _ping(ctx):
        return CONTINUE

    class PingPlugin(Plugin):
        name = "pingable"
        description = "contributes a command"

        def build(self, ctx):
            ctx.register(Command("/ping", "Ping", handler=_ping))
"""


class _PingPlugin(Plugin):
    """The same contribution, without going through a plugin directory."""

    name = "pingable"
    description = "contributes a command"

    def build(self, ctx):
        from mocode.host.command import CONTINUE, Command

        async def _ping(command_ctx):
            return CONTINUE

        ctx.register(Command("/ping", "Ping", handler=_ping))


def _write_plugin(root: Path, name: str, code: str, *, frontmatter: str = "") -> Path:
    """Create ``root/<name>/`` with PLUGIN.md and plugin.py."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    meta = frontmatter or f"---\nname: {name}\ndescription: {name} plugin\n---\n"
    (plugin_dir / "PLUGIN.md").write_text(meta, encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return plugin_dir


def _ctx(tmp_path: Path, **config_kwargs) -> HostContext:
    config = Config(active_provider="p", active_model="m", **config_kwargs)
    return HostContext(home=tmp_path / "home", cwd=tmp_path, config=config)


def _run_host(ctx: HostContext, plugin_dirs: list[Path]) -> PluginHost:
    host = PluginHost(ctx, plugin_dirs=plugin_dirs)
    host.run(provider=MockProvider(), config=AgentConfig())
    return host


# ── frontmatter ─────────────────────────────────────────────


class TestFrontmatter:
    def test_parses_fields(self):
        fm, body = parse_frontmatter("---\nname: x\nenabled: false\n---\n\nDocs here")
        assert fm == {"name": "x", "enabled": False}
        assert body == "Docs here"

    def test_missing_frontmatter(self):
        assert parse_frontmatter("plain") == ({}, "plain")


# ── discovery ───────────────────────────────────────────────


class TestDiscovery:
    def test_finds_directory_plugins(self, tmp_path: Path):
        _write_plugin(tmp_path, "greet", GREET_CODE)
        specs = discover([tmp_path])
        assert [s.name for s in specs] == ["greet"]
        assert specs[0].description == "greet plugin"
        assert specs[0].path.name == "plugin.py"

    def test_finds_single_file_plugins(self, tmp_path: Path):
        (tmp_path / "solo.py").write_text(textwrap.dedent(GREET_CODE), encoding="utf-8")
        assert [s.name for s in discover([tmp_path])] == ["solo"]

    def test_metadata_only_plugin_has_no_code(self, tmp_path: Path):
        plugin_dir = tmp_path / "docs-only"
        plugin_dir.mkdir()
        (plugin_dir / "PLUGIN.md").write_text("---\nname: docs-only\n---\n", encoding="utf-8")
        specs = discover([tmp_path])
        assert specs[0].path is None
        assert load_plugin(specs[0]) is None

    def test_reserved_names_are_skipped(self, tmp_path: Path):
        _write_plugin(tmp_path, "shell", GREET_CODE)
        assert discover([tmp_path], reserved={"shell"}) == []

    def test_local_wins_over_global(self, tmp_path: Path):
        local, global_ = tmp_path / "local", tmp_path / "global"
        _write_plugin(local, "dup", GREET_CODE,
                      frontmatter="---\nname: dup\ndescription: from local\n---\n")
        _write_plugin(global_, "dup", GREET_CODE,
                      frontmatter="---\nname: dup\ndescription: from global\n---\n")

        specs = discover([local, global_])
        assert len(specs) == 1
        assert specs[0].description == "from local"

    def test_ignores_directories_without_manifest(self, tmp_path: Path):
        (tmp_path / "junk").mkdir()
        assert discover([tmp_path]) == []


# ── loading ─────────────────────────────────────────────────


class TestLoading:
    def test_entrypoint_selects_the_class(self, tmp_path: Path):
        _write_plugin(
            tmp_path,
            "multi",
            """
            from mocode.plugins import Plugin

            class Ignored(Plugin):
                name = "ignored"

            class Chosen(Plugin):
                name = "chosen"
            """,
            frontmatter="---\nname: multi\nentrypoint: Chosen\n---\n",
        )
        assert load_plugin(discover([tmp_path])[0]).name == "chosen"

    def test_module_level_instance_wins_over_first_class(self, tmp_path: Path):
        _write_plugin(
            tmp_path,
            "inst",
            """
            from mocode.plugins import Plugin

            class Helper(Plugin):
                name = "helper"

            class Real(Plugin):
                name = "real"

            plugin = Real()
            """,
            frontmatter="---\nname: inst\n---\n",
        )
        assert load_plugin(discover([tmp_path])[0]).name == "real"

    def test_import_error_is_contained(self, tmp_path: Path, capsys):
        _write_plugin(tmp_path, "broken", "raise RuntimeError('boom')")
        assert load_plugin(discover([tmp_path])[0]) is None
        assert "boom" in capsys.readouterr().err


# ── host ────────────────────────────────────────────────────


class TestPluginHost:
    def test_builtins_are_loaded_and_contributing(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        agent = PluginHost(ctx, plugin_dirs=[]).run(
            provider=MockProvider(), config=AgentConfig()
        )

        assert sorted(ctx.tools.names()) == ["bash", "edit", "read", "skill", "write"]
        assert ctx.agent is agent
        assert "<system-prompt>" in agent.system_prompt

    def test_a_plugin_contributes_commands_without_a_terminal(self, tmp_path: Path):
        """An embedding host drives commands itself; only rendering needs a display."""
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "pingable", COMMAND_CODE)

        ctx = _ctx(tmp_path)
        _run_host(ctx, [plugins_dir])

        assert "/ping" in {c.name for c in ctx.commands.all()}

    def test_directory_plugin_is_built(self, tmp_path: Path):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "greet", GREET_CODE)
        ctx = _ctx(tmp_path)
        _run_host(ctx, [plugins_dir])
        assert "greet" in ctx.tools.names()

    def test_disabled_plugin_contributes_nothing(self, tmp_path: Path):
        ctx = _ctx(tmp_path, plugins={"shell": {"enabled": False}})
        _run_host(ctx, [])
        assert "bash" not in ctx.tools.names()

    def test_config_enabled_overrides_the_manifest(self, tmp_path: Path):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "greet", GREET_CODE,
                      frontmatter="---\nname: greet\nenabled: false\n---\n")

        off = _ctx(tmp_path)
        _run_host(off, [plugins_dir])
        assert "greet" not in off.tools.names()

        on = _ctx(tmp_path, plugins={"greet": {"enabled": True}})
        _run_host(on, [plugins_dir])
        assert "greet" in on.tools.names()

    def test_build_failure_is_isolated(self, tmp_path: Path, capsys):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(
            plugins_dir,
            "explodes",
            """
            from mocode.plugins import Plugin

            class Boom(Plugin):
                name = "explodes"

                def build(self, ctx):
                    raise RuntimeError("bad build")
            """,
        )
        _write_plugin(plugins_dir, "greet", GREET_CODE)

        ctx = _ctx(tmp_path)
        host = _run_host(ctx, [plugins_dir])

        assert host.failures == ["explodes"]
        assert "greet" in ctx.tools.names()  # the healthy plugin still built
        assert "bad build" in capsys.readouterr().err

    def test_plugin_config_is_readable_by_plugins(self, tmp_path: Path):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(
            plugins_dir,
            "configured",
            """
            from mocode.plugins import Plugin

            class Configured(Plugin):
                name = "configured"

                def build(self, ctx):
                    assert ctx.plugin_config("configured") == {"greeting": "hi"}
            """,
        )
        ctx = _ctx(tmp_path, plugins={"configured": {"greeting": "hi"}})
        _run_host(ctx, [plugins_dir])
        assert ctx.plugin_config("configured") == {"greeting": "hi"}

    def test_plugin_can_read_the_model_at_build_time(self, tmp_path: Path):
        """Model facts are known before assembly, so build() may budget with them."""
        plugins_dir = tmp_path / "plugins"
        _write_plugin(
            plugins_dir,
            "model-aware",
            """
            from mocode.plugins import Plugin

            class ModelAware(Plugin):
                name = "model-aware"
                seen = None

                def build(self, ctx):
                    type(self).seen = ctx.model.context_window
            """,
        )
        ctx = _ctx(tmp_path)
        ctx.model = ModelSpec(name="m", context_window=128_000, max_output=8192)
        _run_host(ctx, [plugins_dir])

        from mocode_plugin_model_aware import ModelAware  # the loaded plugin module

        assert ModelAware.seen == 128_000
        assert ctx.agent.model is ctx.model

    def test_builtin_registry_is_stable(self):
        """The terminal's plugin is not here — a frontend passes its own in."""
        assert [p.name for p in builtin_plugins()] == ["filesystem", "shell", "skills"]

    def test_extra_plugins_are_loaded_after_the_builtins(self, tmp_path: Path):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "greet", GREET_CODE)
        ctx = _ctx(tmp_path)

        host = PluginHost(ctx, plugin_dirs=[plugins_dir], extra_plugins=[_PingPlugin()])
        host.run(provider=MockProvider(), config=AgentConfig())

        assert [p.name for p in host.plugins] == [
            "filesystem", "shell", "skills", "pingable", "greet",
        ]

    def test_extra_plugins_honour_the_enabled_flag(self, tmp_path: Path):
        ctx = _ctx(tmp_path, plugins={"pingable": {"enabled": False}})
        host = PluginHost(ctx, plugin_dirs=[], extra_plugins=[_PingPlugin()])
        host.run(provider=MockProvider(), config=AgentConfig())

        assert "pingable" not in [p.name for p in host.plugins]
        assert "/ping" not in {c.name for c in ctx.commands.all()}


# ── context ─────────────────────────────────────────────────


class TestHostContext:
    def test_registries_are_created_on_demand(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        assert ctx.tools.names() == []
        assert ctx.commands.all() == []

    def test_agent_starts_unset(self, tmp_path: Path):
        assert _ctx(tmp_path).agent is None

    def test_register_helper_adds_commands(self, tmp_path: Path):
        from mocode.host.command import CONTINUE, Command

        async def _noop(ctx):
            return CONTINUE

        ctx = _ctx(tmp_path)
        ctx.register(Command("/x", "test", handler=_noop))
        assert [c.name for c in ctx.commands.all()] == ["/x"]

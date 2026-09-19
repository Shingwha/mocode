"""The plugin framework — layout, discovery, loading, namespaces, assembly."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from mocode.core.agent import AgentConfig
from mocode.core.provider import ModelSpec
from mocode.host.config import Config
from mocode.host.plugin.context import HostContext
from mocode.host.plugin.host import PluginHost, builtin_plugins, load_plugins
from mocode.host.plugin.loader import (
    HOST_NAMESPACE,
    discover,
    load_plugin,
    namespace_dir,
    read_manifest,
    valid_name,
)

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


def _write_plugin(
    root: Path,
    name: str,
    code: str = "",
    *,
    manifest: dict | None = None,
    raw_manifest: str | None = None,
    skills: list[str] | None = None,
) -> Path:
    """Create ``root/<name>/`` in the Agent Plugins layout."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if raw_manifest is not None:
        (plugin_dir / "plugin.json").write_text(raw_manifest, encoding="utf-8")
    else:
        data = {"$schema": "https://agent-plugins.org/schemas/v1.json", "name": name}
        data.update(manifest or {})
        (plugin_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")

    if code:
        module = plugin_dir / HOST_NAMESPACE / "plugin.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(code), encoding="utf-8")

    for skill in skills or []:
        skill_dir = plugin_dir / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: from a plugin\n---\n\nDo {skill}.",
            encoding="utf-8",
        )
    return plugin_dir


def _ctx(tmp_path: Path, **config_kwargs) -> HostContext:
    config = Config(active_provider="p", active_model="m", **config_kwargs)
    return HostContext(home=tmp_path / "home", cwd=tmp_path, config=config)


def _load(ctx: HostContext, plugin_dirs: list[Path]):
    """What a runtime does for one project: load once, then build."""
    loaded = load_plugins(plugin_dirs=plugin_dirs, config=ctx.config)
    ctx.plugin_sources = list(loaded.sources)
    return PluginHost(ctx, loaded.plugins)


def _run_host(ctx: HostContext, plugin_dirs: list[Path]) -> PluginHost:
    host = _load(ctx, plugin_dirs)
    host.run(provider=MockProvider(), config=AgentConfig())
    return host


# ── the manifest ────────────────────────────────────────────


class TestManifest:
    def test_reads_the_standard_fields(self, tmp_path: Path):
        _write_plugin(tmp_path, "greet", manifest={"version": "1.2.0", "description": "hi"})
        spec = read_manifest(tmp_path / "greet" / "plugin.json")
        assert spec is not None
        assert spec.name == "greet"
        assert spec.version == "1.2.0"
        assert spec.description == "hi"

    def test_a_missing_name_rejects_the_plugin(self, tmp_path: Path, capsys):
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps({"version": "1"}), encoding="utf-8")
        assert read_manifest(path) is None
        assert "invalid name" in capsys.readouterr().err

    def test_unknown_top_level_fields_are_reported_and_ignored(
        self, tmp_path: Path, capsys
    ):
        """The manifest schema is closed — extras belong under `extensions`."""
        path = tmp_path / "plugin.json"
        path.write_text(
            json.dumps({"name": "greet", "enabled": False, "entrypoint": "X"}),
            encoding="utf-8",
        )
        spec = read_manifest(path)
        assert spec is not None and spec.name == "greet"
        err = capsys.readouterr().err
        assert "enabled" in err and "entrypoint" in err

    def test_a_broken_manifest_rejects_the_plugin(self, tmp_path: Path, capsys):
        path = tmp_path / "plugin.json"
        path.write_text("{not json", encoding="utf-8")
        assert read_manifest(path) is None
        assert "unreadable manifest" in capsys.readouterr().err

    def test_name_rules(self):
        assert valid_name("greet")
        assert valid_name("acme.tools")
        assert valid_name("a")
        assert not valid_name("Greet")
        assert not valid_name("-greet")
        assert not valid_name("gre--et")
        assert not valid_name("gre..et")
        assert not valid_name("x" * 65)


# ── discovery ───────────────────────────────────────────────


class TestDiscovery:
    def test_finds_a_plugin_directory(self, tmp_path: Path):
        _write_plugin(tmp_path, "greet", GREET_CODE, manifest={"description": "greets"})
        specs = discover([tmp_path])
        assert [s.name for s in specs] == ["greet"]
        assert specs[0].description == "greets"
        assert specs[0].directory == tmp_path / "greet"
        assert specs[0].module == tmp_path / "greet" / HOST_NAMESPACE / "plugin.py"

    def test_a_plugin_without_code_is_still_a_plugin(self, tmp_path: Path):
        """`skills/` alone is a plugin — the standard's portable component."""
        _write_plugin(tmp_path, "kit", skills=["deploy"])
        spec = discover([tmp_path])[0]
        assert spec.module is None
        assert load_plugin(spec) is None

    def test_finds_single_file_plugins(self, tmp_path: Path):
        (tmp_path / "solo.py").write_text(textwrap.dedent(GREET_CODE), encoding="utf-8")
        assert [s.name for s in discover([tmp_path])] == ["solo"]

    def test_a_directory_without_a_manifest_is_not_a_plugin(self, tmp_path: Path):
        junk = tmp_path / "junk"
        junk.mkdir()
        (junk / "plugin.py").write_text("", encoding="utf-8")
        assert discover([tmp_path]) == []

    def test_reserved_names_are_skipped(self, tmp_path: Path):
        _write_plugin(tmp_path, "shell", GREET_CODE)
        assert discover([tmp_path], reserved={"shell"}) == []

    def test_local_wins_over_global(self, tmp_path: Path):
        local, global_ = tmp_path / "local", tmp_path / "global"
        _write_plugin(local, "dup", manifest={"description": "from local"})
        _write_plugin(global_, "dup", manifest={"description": "from global"})

        specs = discover([local, global_])
        assert len(specs) == 1
        assert specs[0].description == "from local"

    def test_namespace_directories_are_offered_not_read(self, tmp_path: Path):
        plugin_dir = _write_plugin(tmp_path, "greet", GREET_CODE)
        (plugin_dir / "mocode.cli").mkdir()
        (plugin_dir / "mocode.cli" / "plugin.py").write_text("", encoding="utf-8")

        spec = discover([tmp_path])[0]
        assert namespace_dir(spec, "mocode.cli") == plugin_dir / "mocode.cli"
        assert namespace_dir(spec, "mocode.web") is None


# ── loading ─────────────────────────────────────────────────


class TestLoading:
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
        )
        assert load_plugin(discover([tmp_path])[0]).name == "real"

    def test_first_subclass_is_used_when_there_is_no_instance(self, tmp_path: Path):
        _write_plugin(
            tmp_path,
            "multi",
            """
            from mocode.plugins import Plugin

            class Real(Plugin):
                name = "real"
            """,
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
        host = _load(ctx, [])
        agent = host.run(provider=MockProvider(), config=AgentConfig())

        assert sorted(ctx.tools.names()) == ["bash", "edit", "read", "skill", "write"]
        assert ctx.agent is agent
        assert "<system-prompt>" in agent.system_prompt

    def test_a_plugin_contributes_commands_without_a_terminal(self, tmp_path: Path):
        """A command contributed here is shared: any frontend can dispatch it."""
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

    def test_disabling_a_plugin_hides_its_sources_too(self, tmp_path: Path):
        """A disabled plugin contributes nothing — namespace or skills either."""
        plugins_dir = tmp_path / "plugins"
        plugin_dir = _write_plugin(plugins_dir, "greet", GREET_CODE)

        ctx = _ctx(tmp_path, plugins={"greet": {"enabled": False}})
        _run_host(ctx, [plugins_dir])

        assert "greet" not in ctx.tools.names()
        assert plugin_dir not in ctx.plugin_sources

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

    def test_a_plugin_ships_portable_skills(self, tmp_path: Path):
        """`skills/` inside a plugin travels with it, wherever it is installed."""
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "kit", skills=["deploy"])

        ctx = _ctx(tmp_path)
        _run_host(ctx, [plugins_dir])

        assert "/skill:deploy" in {c.name for c in ctx.commands.all()}

    def test_a_user_skill_shadows_a_plugin_one(self, tmp_path: Path):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "kit", skills=["deploy"])

        user_skills = tmp_path / ".mocode" / "skills" / "deploy"
        user_skills.mkdir(parents=True)
        (user_skills / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: mine\n---\n\nMine.", encoding="utf-8"
        )

        ctx = _ctx(tmp_path)
        _run_host(ctx, [plugins_dir])

        command = ctx.commands.get("/skill:deploy")
        assert command is not None and command.description == "mine"

    def test_plugin_source_reaches_the_files_a_plugin_ships(self, tmp_path: Path):
        """A plugin reads its own files through ctx.plugin_sources."""
        plugins_dir = tmp_path / "plugins"
        plugin_dir = _write_plugin(
            plugins_dir,
            "shipper",
            """
            from mocode.plugins import Plugin

            class Shipper(Plugin):
                name = "shipper"

                def build(self, ctx):
                    assert (ctx.plugin_sources[0] / "data" / "notes.txt").is_file()
            """,
        )
        (plugin_dir / "data").mkdir()
        (plugin_dir / "data" / "notes.txt").write_text("hi", encoding="utf-8")

        ctx = _ctx(tmp_path)
        _run_host(ctx, [plugins_dir])
        assert ctx.plugin_sources == [plugin_dir]


# ── the plugin set and its lifecycle ────────────────────────


class TestPluginSet:
    def test_builtin_registry_is_stable(self):
        assert [p.name for p in builtin_plugins()] == ["filesystem", "shell", "skills"]

    def test_loaded_once_and_built_per_conversation(self, tmp_path: Path):
        """Loading is per project; building is per conversation.

        Two conversations in one project share the plugin *instances* and share
        nothing they created — which is what makes a shell session, a skill
        index or any other piece of per-conversation state safe.
        """
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "greet", GREET_CODE)

        loaded = load_plugins(plugin_dirs=[plugins_dir], config=_ctx(tmp_path).config)
        first_ctx, second_ctx = _ctx(tmp_path), _ctx(tmp_path)
        PluginHost(first_ctx, loaded.plugins).run(
            provider=MockProvider(), config=AgentConfig()
        )
        PluginHost(second_ctx, loaded.plugins).run(
            provider=MockProvider(), config=AgentConfig()
        )

        assert first_ctx.tools.get("greet") is not second_ctx.tools.get("greet")
        assert first_ctx.tools.get("bash") is not second_ctx.tools.get("bash")

    def test_close_reaches_every_plugin(self, tmp_path: Path):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(
            plugins_dir,
            "keeper",
            """
            from mocode.plugins import Plugin

            class Keeper(Plugin):
                name = "keeper"
                closed = []

                def close(self, ctx):
                    type(self).closed.append(str(ctx.cwd))
            """,
        )
        ctx = _ctx(tmp_path)
        host = _load(ctx, [plugins_dir])
        host.run(provider=MockProvider(), config=AgentConfig())

        host.close()

        from mocode_plugin_keeper import Keeper

        assert Keeper.closed == [str(tmp_path)]


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

    @pytest.mark.asyncio
    async def test_emit_needs_an_assembled_agent(self, tmp_path: Path):
        from mocode.core.events import Notice

        with pytest.raises(RuntimeError, match="assembled"):
            await _ctx(tmp_path).emit(Notice(message="too early"))

    def test_subscribe_needs_an_assembled_agent(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="assembled"):
            _ctx(tmp_path).subscribe()

    @pytest.mark.asyncio
    async def test_subscribe_reads_a_turn_out_of_band(self, tmp_path: Path):
        """The plugin-facing observation path: everything a turn published."""
        ctx = _ctx(tmp_path)
        _run_host(ctx, [])
        assert ctx.agent is not None

        reader = ctx.subscribe()
        try:
            await ctx.agent.chat("hi")
            seen = []
            while (event := reader.take()) is not None:
                seen.append(event.type)
        finally:
            reader.close()

        assert seen[0] == "run_started"
        assert "text_delta" in seen
        assert seen[-1] == "run_finished"

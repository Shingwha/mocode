"""The plugin framework — layout, discovery, loading, namespaces, assembly."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path


from mocode.core.agent import AgentConfig
from mocode.core.provider import ModelSpec
from mocode.host.config import Config
from mocode.host.plugin.context import BuildContext, HostContext
from mocode.host.plugin.host import PluginHost, builtin_plugins, load_plugins
from mocode.host.plugin.loader import (
    HOST_NAMESPACE,
    discover,
    load_plugin,
    namespace_dir,
    read_manifest,
    valid_name,
)
from mocode.testing import MockProvider

from .conftest import write_plugin

GREET_CODE = """
    from mocode.plugins import Plugin, Tool

    class GreetTool(Tool):
        def __init__(self):
            super().__init__(
                name="greet", description="Greet", schema={"type": "object", "properties": {}},
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
            ctx.commands.register(Command("/ping", "Ping", handler=_ping))
"""

#: A plugin written as a package: the entry is ``mocode/plugin/__init__.py``
#: and the submodule is reachable only through a relative import.
PACKAGE_PLUGIN = {
    "__init__.py": """
    from mocode.plugins import Plugin

    from .helper import GREETING

    class Packaged(Plugin):
        name = "packaged"
        description = "loads from a package"

    plugin = Packaged()
    """,
    "helper.py": "GREETING = 'hello from a submodule'\n",
}


def _ctx(tmp_path: Path, **config_kwargs) -> BuildContext:
    config = Config(provider="p", model="m", **config_kwargs)
    return BuildContext(home=tmp_path / "home", cwd=tmp_path, config=config)


def _load(ctx: BuildContext, plugin_dirs: list[Path]):
    """What a runtime does for one project: load once, then build."""
    loaded = load_plugins(plugin_dirs=plugin_dirs, config=ctx.config)
    ctx.plugin_sources = list(loaded.sources)
    return PluginHost(ctx, loaded.plugins)


# ── the manifest ────────────────────────────────────────────


class TestManifest:
    def test_reads_the_standard_fields(self, tmp_path: Path):
        write_plugin(tmp_path, "greet", manifest={"version": "1.2.0", "description": "hi"})
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
        write_plugin(tmp_path, "greet", GREET_CODE, manifest={"description": "greets"})
        specs = discover([tmp_path])
        assert [s.name for s in specs] == ["greet"]
        assert specs[0].description == "greets"
        assert specs[0].directory == tmp_path / "greet"
        assert specs[0].module == tmp_path / "greet" / HOST_NAMESPACE / "plugin.py"

    def test_a_plugin_without_code_is_still_a_plugin(self, tmp_path: Path):
        """`skills/` alone is a plugin — the standard's portable component."""
        write_plugin(tmp_path, "kit", skills=["deploy"])
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
        write_plugin(tmp_path, "shell", GREET_CODE)
        assert discover([tmp_path], reserved={"shell"}) == []

    def test_local_wins_over_global(self, tmp_path: Path):
        local, global_ = tmp_path / "local", tmp_path / "global"
        write_plugin(local, "dup", manifest={"description": "from local"})
        write_plugin(global_, "dup", manifest={"description": "from global"})

        specs = discover([local, global_])
        assert len(specs) == 1
        assert specs[0].description == "from local"

    def test_namespace_directories_are_offered_not_read(self, tmp_path: Path):
        plugin_dir = write_plugin(tmp_path, "greet", GREET_CODE)
        (plugin_dir / "mocode.cli").mkdir()
        (plugin_dir / "mocode.cli" / "plugin.py").write_text("", encoding="utf-8")

        spec = discover([tmp_path])[0]
        assert namespace_dir(spec, "mocode.cli") == plugin_dir / "mocode.cli"
        assert namespace_dir(spec, "mocode.web") is None


# ── loading ─────────────────────────────────────────────────


class TestLoading:
    def test_module_level_instance_wins_over_first_class(self, tmp_path: Path):
        write_plugin(
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
        write_plugin(
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
        write_plugin(tmp_path, "broken", "raise RuntimeError('boom')")
        assert load_plugin(discover([tmp_path])[0]) is None
        assert "boom" in capsys.readouterr().err


# ── the package form ────────────────────────────────────────


class TestPackagePlugins:
    def test_a_package_entry_loads(self, tmp_path: Path):
        write_plugin(tmp_path, "packaged", package=PACKAGE_PLUGIN)
        spec = discover([tmp_path])[0]
        assert spec.module is not None and spec.module.name == "__init__.py"
        assert load_plugin(spec).name == "packaged"

    def test_submodules_load_by_relative_import(self, tmp_path: Path):
        """`from .helper import x` inside the package, under the plugin's own name."""
        write_plugin(tmp_path, "packaged", package=PACKAGE_PLUGIN)
        load_plugin(discover([tmp_path])[0])

        from mocode_plugin_packaged.helper import GREETING

        assert GREETING == "hello from a submodule"

    def test_two_plugins_may_ship_same_named_submodules(self, tmp_path: Path):
        """Each plugin's package lives under its own name — no sys.modules race."""
        for name in ("one", "two"):
            write_plugin(
                tmp_path,
                name,
                package={
                    "__init__.py": f"""
                    from mocode.plugins import Plugin

                    from .helper import WHO


                    class P(Plugin):
                        name = "{name}"
                        description = WHO

                    plugin = P()
                    """,
                    "helper.py": f"WHO = '{name}'\n",
                },
            )

        plugins = {p.name: p for p in map(load_plugin, discover([tmp_path]))}

        assert plugins["one"].description == "one"
        assert plugins["two"].description == "two"
        assert "mocode_plugin_one.helper" in sys.modules
        assert "mocode_plugin_two.helper" in sys.modules

    def test_the_single_file_wins_when_both_exist(self, tmp_path: Path):
        write_plugin(tmp_path, "both", GREET_CODE, package=PACKAGE_PLUGIN)
        spec = discover([tmp_path])[0]
        assert spec.module is not None and spec.module.name == "plugin.py"
        assert load_plugin(spec).name == "greet"

    def test_a_package_without_init_is_reported_not_skipped(
        self, tmp_path: Path, capsys
    ):
        plugin_dir = write_plugin(tmp_path, "no-init")
        package = plugin_dir / HOST_NAMESPACE / "plugin"
        package.mkdir(parents=True)
        (package / "helper.py").write_text("x = 1", encoding="utf-8")

        spec = discover([tmp_path])[0]

        assert spec.module is None
        assert "plugin/__init__.py" in capsys.readouterr().err

    def test_stray_modules_beside_no_entry_are_reported(
        self, tmp_path: Path, capsys
    ):
        plugin_dir = write_plugin(tmp_path, "stray")
        namespace = plugin_dir / HOST_NAMESPACE
        namespace.mkdir(parents=True)
        (namespace / "helpers.py").write_text("x = 1", encoding="utf-8")

        spec = discover([tmp_path])[0]

        assert spec.module is None
        assert "helpers.py" in capsys.readouterr().err

    def test_a_namespace_that_ships_nothing_stays_quiet(self, tmp_path: Path, capsys):
        """An empty namespace directory is not a near-miss — nothing to fix."""
        plugin_dir = write_plugin(tmp_path, "empty")
        (plugin_dir / HOST_NAMESPACE).mkdir()

        assert discover([tmp_path])[0].module is None
        assert capsys.readouterr().err == ""

    def test_a_broken_package_leaves_no_submodules_behind(
        self, tmp_path: Path, capsys
    ):
        write_plugin(
            tmp_path,
            "half-broken",
            package={
                "__init__.py": "from .half import x\nraise RuntimeError('boom')\n",
                "half.py": "x = 1\n",
            },
        )
        assert load_plugin(discover([tmp_path])[0]) is None
        assert "mocode_plugin_half_broken" not in sys.modules
        assert "mocode_plugin_half_broken.half" not in sys.modules


# ── the shipped multi-file example ──────────────────────────


class TestTheMultiFileExample:
    """examples/plugins/multi-file — the proof the package form works whole."""

    EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "plugins"

    def test_the_example_is_loaded_and_built(
        self, tmp_path: Path, plugin_host
    ):
        ctx = _ctx(tmp_path)
        loaded = load_plugins(plugin_dirs=[self.EXAMPLES], config=ctx.config)

        assert "multi-file" in [p.name for p in loaded.plugins]

        host = plugin_host(plugins=loaded.plugins)

        assert "/motd" in {c.name for c in host.ctx.commands.all()}
        assert "motd" in {s.name for s in host.ctx.prompt_sections}
        assert "motd" not in host.ctx.tools.names()  # the example registers no tools


# ── host ────────────────────────────────────────────────────


class TestPluginHost:
    async def test_builtins_are_loaded_and_contributing(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        host = _load(ctx, [])
        host.build_all()
        agent = host.assemble(provider=MockProvider(), config=AgentConfig())
        await host.materialize()

        assert sorted(ctx.tools.names()) == [
            "bash",
            "bash_output",
            "codemode",
            "edit",
            "kill_shell",
            "read",
            "skill",
            "write",
        ]
        assert ctx.agent is agent
        assert "<system-prompt>" in agent.system_prompt

    def test_a_plugin_contributes_commands_without_a_terminal(
        self, tmp_path: Path, plugin_host
    ):
        """A command contributed here is shared: any frontend can dispatch it."""
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "pingable", COMMAND_CODE)

        host = plugin_host(load=[plugins_dir])

        assert "/ping" in {c.name for c in host.ctx.commands.all()}

    def test_directory_plugin_is_built(self, tmp_path: Path, plugin_host):
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "greet", GREET_CODE)
        host = plugin_host(load=[plugins_dir])
        assert "greet" in host.ctx.tools.names()

    def test_disabled_plugin_contributes_nothing(self, tmp_path: Path, plugin_host):
        host = plugin_host(
            config_kwargs={"plugins": {"shell": {"enabled": False}}}, load=[]
        )
        assert "bash" not in host.ctx.tools.names()

    def test_disabling_a_plugin_hides_its_sources_too(
        self, tmp_path: Path, plugin_host
    ):
        """A disabled plugin contributes nothing — namespace or skills either."""
        plugins_dir = tmp_path / "plugins"
        plugin_dir = write_plugin(plugins_dir, "greet", GREET_CODE)

        host = plugin_host(
            config_kwargs={"plugins": {"greet": {"enabled": False}}}, load=[plugins_dir]
        )

        assert "greet" not in host.ctx.tools.names()
        assert plugin_dir not in host.ctx.plugin_sources

    def test_build_failure_is_isolated(self, tmp_path: Path, plugin_host, capsys):
        plugins_dir = tmp_path / "plugins"
        write_plugin(
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
        write_plugin(plugins_dir, "greet", GREET_CODE)

        host = plugin_host(load=[plugins_dir])

        assert host.failures == ["explodes"]
        assert "greet" in host.ctx.tools.names()  # the healthy plugin still built
        assert "bad build" in capsys.readouterr().err

    def test_plugin_config_is_readable_by_plugins(self, tmp_path: Path, plugin_host):
        plugins_dir = tmp_path / "plugins"
        write_plugin(
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
        host = plugin_host(
            config_kwargs={"plugins": {"configured": {"greeting": "hi"}}},
            load=[plugins_dir],
        )
        assert host.ctx.plugin_config("configured") == {"greeting": "hi"}

    def test_a_plugin_ships_portable_skills(self, tmp_path: Path, plugin_host):
        """`skills/` inside a plugin travels with it, wherever it is installed."""
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "kit", skills=["deploy"])

        host = plugin_host(load=[plugins_dir])

        assert "/skill:deploy" in {c.name for c in host.ctx.commands.all()}

    def test_a_user_skill_shadows_a_plugin_one(self, tmp_path: Path, plugin_host):
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "kit", skills=["deploy"])

        user_skills = tmp_path / ".mocode" / "skills" / "deploy"
        user_skills.mkdir(parents=True)
        (user_skills / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: mine\n---\n\nMine.", encoding="utf-8"
        )

        host = plugin_host(load=[plugins_dir])

        command = host.ctx.commands.get("/skill:deploy")
        assert command is not None and command.description == "mine"

    def test_plugin_source_reaches_the_files_a_plugin_ships(
        self, tmp_path: Path, plugin_host
    ):
        """A plugin reads its own files through ctx.plugin_sources."""
        plugins_dir = tmp_path / "plugins"
        plugin_dir = write_plugin(
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

        host = plugin_host(load=[plugins_dir])
        assert host.ctx.plugin_sources == [plugin_dir]


# ── the plugin set and its lifecycle ────────────────────────


class TestPluginSet:
    def test_builtin_registry_is_stable(self):
        assert [p.name for p in builtin_plugins()] == [
            "filesystem",
            "shell",
            "skills",
            "mcp",
            "codemode",
            "default-prompts",
            "session",
            "help",
            "effort",
            "cache-protect",
        ]

    def test_loaded_once_and_built_per_conversation(
        self, tmp_path: Path, plugin_host
    ):
        """Loading is per project; building is per conversation.

        Two conversations in one project share the plugin *instances* and share
        nothing they created — which is what makes a shell session, a skill
        index or any other piece of per-conversation state safe.
        """
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "greet", GREET_CODE)

        loaded = load_plugins(plugin_dirs=[plugins_dir], config=_ctx(tmp_path).config)
        first = plugin_host(plugins=loaded.plugins)
        second = plugin_host(plugins=loaded.plugins)

        assert first.ctx.tools.get("greet") is not second.ctx.tools.get("greet")
        assert first.ctx.tools.get("bash") is not second.ctx.tools.get("bash")

    def test_close_reaches_every_plugin(self, tmp_path: Path, plugin_host):
        plugins_dir = tmp_path / "plugins"
        write_plugin(
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
        host = plugin_host(load=[plugins_dir])

        host.close()

        from mocode_plugin_keeper import Keeper

        assert Keeper.closed == [str(tmp_path)]


# ── context ─────────────────────────────────────────────────


class TestHostContext:
    def test_registries_are_created_on_demand(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        assert ctx.tools.names() == []
        assert ctx.commands.all() == []

    def test_build_context_has_no_agent_and_assembly_grows_it(self, tmp_path: Path):
        """The stage split, as a runtime fact: no agent during build(), the
        same object carries one after assembly."""
        ctx = _ctx(tmp_path)
        assert not hasattr(ctx, "agent")

        grown = PluginHost(ctx, []).assemble(
            provider=MockProvider(), config=AgentConfig()
        )

        assert isinstance(ctx, HostContext)  # grown in place, same object
        assert ctx.agent is grown

    def test_commands_register_adds_commands(self, tmp_path: Path):
        from mocode.host.command import CONTINUE, Command

        async def _noop(ctx):
            return CONTINUE

        ctx = _ctx(tmp_path)
        ctx.commands.register(Command("/x", "test", handler=_noop))
        assert [c.name for c in ctx.commands.all()] == ["/x"]

    async def test_subscribe_reads_a_turn_out_of_band(
        self, tmp_path: Path, plugin_host
    ):
        """The plugin-facing observation path: everything a turn published."""
        host = plugin_host(load=[])
        assert host.ctx.agent is not None

        reader = host.ctx.subscribe()
        try:
            await host.ctx.agent.chat("hi")
            seen = []
            while (event := reader.take()) is not None:
                seen.append(event.type)
        finally:
            reader.close()

        assert seen[0] == "run_started"
        assert "text_delta" in seen
        assert seen[-1] == "run_finished"

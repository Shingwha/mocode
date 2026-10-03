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
    def test_reads_the_standard_fields_and_reports_extras(
        self, tmp_path: Path, capsys
    ):
        """清单 schema 是封闭的：认识的字段读出来，多出来的顶层字段报出来
        并忽略（它们属于 extensions）。"""
        write_plugin(tmp_path, "greet", manifest={"version": "1.2.0", "description": "hi"})
        spec = read_manifest(tmp_path / "greet" / "plugin.json")
        assert spec is not None
        assert spec.name == "greet"
        assert spec.version == "1.2.0"
        assert spec.description == "hi"

        path = tmp_path / "plugin.json"
        path.write_text(
            json.dumps({"name": "greet", "enabled": False, "entrypoint": "X"}),
            encoding="utf-8",
        )
        extra = read_manifest(path)
        assert extra is not None and extra.name == "greet"
        err = capsys.readouterr().err
        assert "enabled" in err and "entrypoint" in err

    def test_a_malformed_manifest_rejects_the_plugin(self, tmp_path: Path, capsys):
        """缺名字与 JSON 破损都拒收，并说明原因。"""
        cases = [
            (json.dumps({"version": "1"}), "invalid name"),
            ("{not json", "unreadable manifest"),
        ]
        for raw, problem in cases:
            path = tmp_path / "plugin.json"
            path.write_text(raw, encoding="utf-8")
            assert read_manifest(path) is None
            assert problem in capsys.readouterr().err

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
    def test_the_two_module_forms_are_found(self, tmp_path: Path):
        """目录形式与单文件形式都是插件；目录形式解析到它自己的
        mocode/plugin.py。"""
        write_plugin(tmp_path, "greet", GREET_CODE, manifest={"description": "greets"})
        specs = discover([tmp_path])
        assert [s.name for s in specs] == ["greet"]
        assert specs[0].description == "greets"
        assert specs[0].directory == tmp_path / "greet"
        assert specs[0].module == tmp_path / "greet" / HOST_NAMESPACE / "plugin.py"

        solo_root = tmp_path / "solo-root"
        solo_root.mkdir()
        (solo_root / "solo.py").write_text(textwrap.dedent(GREET_CODE), encoding="utf-8")
        assert [s.name for s in discover([solo_root])] == ["solo"]

    def test_directories_that_are_not_plugins_are_skipped(self, tmp_path: Path):
        """没有清单的目录不是插件；占了保留名的也不是；反过来，只有 skills/
        的目录仍然是插件——只是没有代码可加载。"""
        junk = tmp_path / "junk"
        junk.mkdir()
        (junk / "plugin.py").write_text("", encoding="utf-8")
        assert discover([tmp_path]) == []

        write_plugin(tmp_path, "shell", GREET_CODE)
        assert discover([tmp_path], reserved={"shell"}) == []

        write_plugin(tmp_path, "kit", skills=["deploy"])
        [kit] = discover([tmp_path], reserved={"shell"})
        assert kit.module is None
        assert load_plugin(kit) is None

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
    def test_the_entry_point_is_chosen_by_instance_then_first_class(self, tmp_path: Path):
        """入口选择规则：有模块级 ``plugin`` 实例用它；没有，就用第一个
        Plugin 子类。"""
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
        write_plugin(
            tmp_path,
            "multi",
            """
            from mocode.plugins import Plugin

            class Real(Plugin):
                name = "real"
            """,
        )

        specs = {s.name: s for s in discover([tmp_path])}
        assert load_plugin(specs["inst"]).name == "real"
        assert load_plugin(specs["multi"]).name == "real"

    def test_import_error_is_contained(self, tmp_path: Path, capsys):
        write_plugin(tmp_path, "broken", "raise RuntimeError('boom')")
        assert load_plugin(discover([tmp_path])[0]) is None
        assert "boom" in capsys.readouterr().err


# ── the package form ────────────────────────────────────────


class TestPackagePlugins:
    def test_a_package_entry_loads_and_a_single_file_wins_over_it(self, tmp_path: Path):
        """包形式的入口是 ``__init__.py``；单文件与包同时存在时单文件赢。"""
        write_plugin(tmp_path, "packaged", package=PACKAGE_PLUGIN)
        packaged = {s.name: s for s in discover([tmp_path])}
        assert packaged["packaged"].module is not None
        assert packaged["packaged"].module.name == "__init__.py"
        assert load_plugin(packaged["packaged"]).name == "packaged"

        write_plugin(tmp_path, "both", GREET_CODE, package=PACKAGE_PLUGIN)
        both = {s.name: s for s in discover([tmp_path])}
        assert both["both"].module is not None
        assert both["both"].module.name == "plugin.py"
        assert load_plugin(both["both"]).name == "greet"

    def test_submodules_load_by_relative_import_under_the_plugins_own_name(
        self, tmp_path: Path
    ):
        """`from .helper import x` 在包内成立，且解析到插件自己名下的模块
        ——两个插件可以带同名子模块，sys.modules 里互不污染。"""
        write_plugin(tmp_path, "packaged", package=PACKAGE_PLUGIN)
        load_plugin(discover([tmp_path])[0])

        from mocode_plugin_packaged.helper import GREETING

        assert GREETING == "hello from a submodule"

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

    def test_a_namespace_without_an_entry_point_is_reported(self, tmp_path: Path, capsys):
        """命名段里没有入口点的两种形态都点名报出来：包目录缺 __init__、
        散落的模块。空命名目录不是 near-miss——没什么可修，也什么都不说。"""
        empty = write_plugin(tmp_path, "empty")
        (empty / HOST_NAMESPACE).mkdir()

        assert discover([tmp_path])[0].module is None
        assert capsys.readouterr().err == ""

        no_init = write_plugin(tmp_path, "no-init")
        package = no_init / HOST_NAMESPACE / "plugin"
        package.mkdir(parents=True)
        (package / "helper.py").write_text("x = 1", encoding="utf-8")

        stray = write_plugin(tmp_path, "stray")
        namespace = stray / HOST_NAMESPACE
        namespace.mkdir(parents=True)
        (namespace / "helpers.py").write_text("x = 1", encoding="utf-8")

        specs = {s.name: s for s in discover([tmp_path])}
        assert specs["no-init"].module is None
        assert specs["stray"].module is None
        err = capsys.readouterr().err
        assert "plugin/__init__.py" in err
        assert "helpers.py" in err

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

        # 内建集本身固定且有序：名字表即加载顺序
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

    def test_a_loaded_plugin_contributes_tools_and_commands(
        self, tmp_path: Path, plugin_host
    ):
        """从目录加载进来的插件，工具与命令都贡献——贡献出来的命令是共享
        的：任何前端都能派发它。"""
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "pingable", COMMAND_CODE)
        write_plugin(plugins_dir, "greet", GREET_CODE)

        host = plugin_host(load=[plugins_dir])

        assert "/ping" in {c.name for c in host.ctx.commands.all()}
        assert "greet" in host.ctx.tools.names()

    def test_a_disabled_plugin_contributes_nothing(self, tmp_path: Path, plugin_host):
        """禁用的插件什么都不贡献：工具没有；它带的命名段与 skills 的源
        同样不给。"""
        host = plugin_host(
            config_kwargs={"plugins": {"shell": {"enabled": False}}}, load=[]
        )
        assert "bash" not in host.ctx.tools.names()

        plugins_dir = tmp_path / "plugins"
        plugin_dir = write_plugin(plugins_dir, "greet", GREET_CODE)

        hidden = plugin_host(
            config_kwargs={"plugins": {"greet": {"enabled": False}}}, load=[plugins_dir]
        )

        assert "greet" not in hidden.ctx.tools.names()
        assert plugin_dir not in hidden.ctx.plugin_sources

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

    def test_a_plugin_ships_portable_skills_and_a_user_skill_shadows_it(
        self, tmp_path: Path, plugin_host
    ):
        """`skills/` inside a plugin travels with it, wherever it is
        installed——用户自己目录里的同名技能压过插件带的那个。"""
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "kit", skills=["deploy"])

        user_skills = tmp_path / ".mocode" / "skills" / "deploy"
        user_skills.mkdir(parents=True)
        (user_skills / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: mine\n---\n\nMine.", encoding="utf-8"
        )

        host = plugin_host(load=[plugins_dir])

        assert "/skill:deploy" in {c.name for c in host.ctx.commands.all()}
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
    def test_build_context_has_no_agent_and_assembly_grows_it(self, tmp_path: Path):
        """The stage split, as a runtime fact: no agent during build(), the
        same object carries one after assembly — and a fresh context's
        registries are empty until something registers."""
        ctx = _ctx(tmp_path)
        assert not hasattr(ctx, "agent")

        assert ctx.tools.names() == []
        assert ctx.commands.all() == []

        from mocode.host.command import CONTINUE, Command

        async def _noop(ctx):
            return CONTINUE

        ctx.commands.register(Command("/x", "test", handler=_noop))
        assert [c.name for c in ctx.commands.all()] == ["/x"]

        grown = PluginHost(ctx, []).assemble(
            provider=MockProvider(), config=AgentConfig()
        )

        assert isinstance(ctx, HostContext)  # grown in place, same object
        assert ctx.agent is grown

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

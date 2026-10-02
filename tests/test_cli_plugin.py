"""The terminal's plugin surface — a namespace, not a second plugin framework.

One plugin directory can carry both surfaces:

    acme/
      plugin.json            the manifest
      mocode/plugin.py       contributions to the *agent*  → every frontend
      mocode.cli/plugin.py   contributions to the *terminal* → this frontend only

The host hands the ``mocode.cli`` directory over for the terminal to read and
never looks inside it; the terminal reads no other namespace.
"""

from __future__ import annotations

from pathlib import Path


from .conftest import make_config, write_plugin

HOST_CODE = """
    from mocode.plugins import Plugin, Tool

    class PingTool(Tool):
        def __init__(self):
            super().__init__(name="ping", description="d", schema={"type": "object", "properties": {}}, func=lambda a: "pong")

    class PingPlugin(Plugin):
        name = "acme"

        def build(self, ctx):
            ctx.tools.register(PingTool())
"""

CLI_CODE = """
    from mocode.cli import CLIPlugin
    from mocode.host.command import CONTINUE, Command

    class AcmeCommands(CLIPlugin):
        name = "acme.cli"

        def build(self, cli):
            async def _shout(ctx):
                await ctx.conversation.notify(f"shout: {ctx.args}")
                return CONTINUE

            cli.commands.register(Command("/shout", "Shout it", handler=_shout))
            cli.seen_conversation = cli.conversation is not None
"""




def _app(tmp_path: Path, plugins: Path):
    from mocode.cli import CLIApp

    return CLIApp(
        config=make_config(),
        home=tmp_path / "home",
        interactive=True,
        plugin_dirs=[plugins],
    )


class TestBothSurfacesInOneDirectory:
    def test_the_agent_gets_the_host_namespace(self, tmp_path: Path):
        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", HOST_CODE, cli=CLI_CODE)

        app = _app(tmp_path, plugins)

        assert "ping" in app.conversation.tools.names()

    def test_the_terminal_gets_its_own_namespace(self, tmp_path: Path):
        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", HOST_CODE, cli=CLI_CODE)

        app = _app(tmp_path, plugins)

        assert "/shout" in {c.name for c in app.commands.all()}
        assert "acme.cli" in [p.name for p in app.plugins]

    def test_a_cli_plugin_is_built_against_the_terminal(self, tmp_path: Path):
        """It can reach the commands, the screen and the conversation."""
        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", cli=CLI_CODE)

        app = _app(tmp_path, plugins)

        assert app.seen_conversation is True

    async def test_its_command_speaks_on_the_conversations_stream(self, tmp_path: Path):

        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", cli=CLI_CODE)
        app = _app(tmp_path, plugins)
        reader = app.conversation.subscribe()

        await app.commands.dispatch("/shout hello", conversation=app.conversation)

        notice = reader.take()
        assert notice is not None and notice.message == "shout: hello"

    def test_only_the_terminal_reads_the_terminal_namespace(self, tmp_path: Path):
        """The host neither imports it nor knows what is in it."""
        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", HOST_CODE, cli=CLI_CODE)

        app = _app(tmp_path, plugins)

        assert [p.name for p in app.runtime.plugins_for(tmp_path)] == [
            "filesystem", "shell", "skills", "default-prompts", "session", "help",
            "effort", "cache-protect", "acme",
        ]


class TestLoadingRules:
    def test_a_namespace_without_code_is_ignored(self, tmp_path: Path):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", HOST_CODE)

        assert load_cli_plugins([plugins / "acme"]) == []

    def test_a_module_level_plugin_instance_wins(self, tmp_path: Path):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        write_plugin(
            plugins,
            "acme",
            cli="""
            from mocode.cli import CLIPlugin

            class Ignored(CLIPlugin):
                name = "ignored"

            class Real(CLIPlugin):
                name = "real"

            plugin = Real()
            """,
        )

        assert [p.name for p in load_cli_plugins([plugins / "acme"])] == ["real"]

    def test_a_broken_cli_plugin_costs_only_itself(self, tmp_path: Path, capsys):
        from mocode.cli.plugin import build_cli_plugins

        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", cli="raise RuntimeError('boom')")
        app = _app(tmp_path, plugins)

        assert "boom" in capsys.readouterr().err
        assert "/help" in {c.name for c in app.commands.all()}


class TestThePackageForm:
    """The terminal namespace judges its entry the same way the host does."""

    CLI_PACKAGE = {
        "__init__.py": """
        from mocode.cli import CLIPlugin

        from .title import TITLE


        class PackagedCLI(CLIPlugin):
            name = "packaged.cli"
            description = TITLE

        plugin = PackagedCLI()
        """,
        "title.py": "TITLE = 'assembled from a submodule'\n",
    }

    def test_a_cli_namespace_package_loads(self, tmp_path: Path):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", cli_package=self.CLI_PACKAGE)

        loaded = load_cli_plugins([plugins / "acme"])

        assert [p.name for p in loaded] == ["packaged.cli"]
        assert loaded[0].description == "assembled from a submodule"

    def test_the_single_file_wins_when_both_exist(self, tmp_path: Path):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", CLI_CODE, cli=CLI_CODE, cli_package=self.CLI_PACKAGE)

        assert [p.name for p in load_cli_plugins([plugins / "acme"])] == ["acme.cli"]

    def test_a_package_without_init_is_reported_not_skipped(
        self, tmp_path: Path, capsys
    ):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        write_plugin(plugins, "acme", cli_package={"title.py": "TITLE = 'orphaned'\n"})

        assert load_cli_plugins([plugins / "acme"]) == []
        assert "plugin/__init__.py" in capsys.readouterr().err


class TestTheTerminalsOwnCommands:
    def test_they_are_contributed_by_the_terminal_plugin(self, tmp_path: Path):
        """One way to contribute to the terminal — MoCode is not an exception."""
        from mocode.cli.plugin import BuiltinCommands, NAMESPACE

        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)

        assert NAMESPACE == "mocode.cli"
        # A fresh instance per app: two terminals never share one plugin object.
        assert isinstance(app.plugins[0], BuiltinCommands)
        assert app.plugins[0] is not app.plugins[0].__class__()
        assert "/model" in {c.name for c in app.commands.all()}

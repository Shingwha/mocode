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

import json
import textwrap
from pathlib import Path

import pytest

from mocode.core.events import PluginMessage

from .conftest import make_config, strip_ansi

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

        def build(self, ctx):
            self.ctx = ctx
            async def _shout(c):
                await c.conversation.notify(f"shout: {c.args}")
                return CONTINUE

            ctx.commands.register(Command("/shout", "Shout it", handler=_shout))
"""

DRAWER_CODE = """
    from dataclasses import dataclass

    from mocode.cli import CLIPlugin
    from mocode.core import Event


    @dataclass
    class Scored(Event):
        type = "acme/scored"
        score: int = 0


    class AcmeDrawers(CLIPlugin):
        name = "acme.draw"

        def build(self, ctx):
            self.event = Scored

            def draw_scored(event):
                from mocode.cli import lines
                return [lines.Line(text=f"score {event.score}")]

            def draw_progress(event):
                from mocode.cli import lines
                return [lines.Line(text=f"{event.data['done']} done")]

            ctx.drawers.register(Scored, draw_scored)
            ctx.drawers.register("acme/progress", draw_progress)
"""




def _install(
    root: Path,
    name: str = "acme",
    *,
    host: str = "",
    cli: str = "",
    cli_package: dict[str, str] | None = None,
) -> Path:
    plugin = root / name
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    if host:
        module = plugin / "mocode" / "plugin.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(host), encoding="utf-8")
    if cli:
        module = plugin / "mocode.cli" / "plugin.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(cli), encoding="utf-8")
    for filename, text in (cli_package or {}).items():
        module = plugin / "mocode.cli" / "plugin" / filename
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(text), encoding="utf-8")
    return plugin


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
        _install(plugins, host=HOST_CODE, cli=CLI_CODE)

        app = _app(tmp_path, plugins)

        assert "ping" in app.conversation.tools.names()

    def test_the_terminal_gets_its_own_namespace(self, tmp_path: Path):
        plugins = tmp_path / "plugins"
        _install(plugins, host=HOST_CODE, cli=CLI_CODE)

        app = _app(tmp_path, plugins)

        assert "/shout" in {c.name for c in app.commands.all()}
        assert "acme.cli" in [p.name for p in app.plugins]

    def test_a_cli_plugin_is_built_against_the_context(self, tmp_path: Path):
        """A narrow, stable surface: commands, drawers, ui — and no way in."""
        plugins = tmp_path / "plugins"
        _install(plugins, cli=CLI_CODE)

        app = _app(tmp_path, plugins)
        plugin = next(p for p in app.plugins if p.name == "acme.cli")

        from mocode.cli import CLIContext

        assert isinstance(plugin.ctx, CLIContext)
        assert plugin.ctx.commands is app.commands   # the registry the app dispatches from
        assert plugin.ctx.drawers is app.drawers    # the table the renderer reads
        assert plugin.ctx.ui.is_interactive is False   # no terminal under a test pipe
        assert plugin.ctx.input is app.input_middleware  # the chain, not the Input session
        assert plugin.ctx.conversation is app.conversation
        for internal in ("app", "display", "renderer"):
            assert not hasattr(plugin.ctx, internal)

    @pytest.mark.asyncio
    async def test_its_command_speaks_on_the_conversations_stream(self, tmp_path: Path):

        plugins = tmp_path / "plugins"
        _install(plugins, cli=CLI_CODE)
        app = _app(tmp_path, plugins)
        reader = app.conversation.subscribe()

        await app.commands.dispatch("/shout hello", conversation=app.conversation)

        notice = reader.take()
        assert notice is not None and notice.message == "shout: hello"

    def test_only_the_terminal_reads_the_terminal_namespace(self, tmp_path: Path):
        """The host neither imports it nor knows what is in it."""
        plugins = tmp_path / "plugins"
        _install(plugins, host=HOST_CODE, cli=CLI_CODE)

        app = _app(tmp_path, plugins)

        assert [p.name for p in app.runtime.plugins_for(tmp_path)] == [
            "filesystem", "shell", "skills", "default-prompts", "session", "help",
            "cache-protect", "acme",
        ]


class TestLoadingRules:
    def test_a_namespace_without_code_is_ignored(self, tmp_path: Path):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        _install(plugins, host=HOST_CODE)

        assert load_cli_plugins([plugins / "acme"]) == []

    def test_a_module_level_plugin_instance_wins(self, tmp_path: Path):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        _install(
            plugins,
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
        _install(plugins, cli="raise RuntimeError('boom')")
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
        _install(plugins, cli_package=self.CLI_PACKAGE)

        loaded = load_cli_plugins([plugins / "acme"])

        assert [p.name for p in loaded] == ["packaged.cli"]
        assert loaded[0].description == "assembled from a submodule"

    def test_the_single_file_wins_when_both_exist(self, tmp_path: Path):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        _install(plugins, cli=CLI_CODE, cli_package=self.CLI_PACKAGE)

        assert [p.name for p in load_cli_plugins([plugins / "acme"])] == ["acme.cli"]

    def test_a_package_without_init_is_reported_not_skipped(
        self, tmp_path: Path, capsys
    ):
        from mocode.cli.plugin import load_cli_plugins

        plugins = tmp_path / "plugins"
        _install(plugins, cli_package={"title.py": "TITLE = 'orphaned'\n"})

        assert load_cli_plugins([plugins / "acme"]) == []
        assert "plugin/__init__.py" in capsys.readouterr().err


class TestTheFullContext:
    """The T3 surface: keys, input middleware, status, header, theme, on_close."""

    def test_every_member_is_the_apps_own(self, tmp_path):
        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)

        assert app.ctx.keys is app.keys
        assert app.ctx.input is app.input_middleware
        assert app.ctx.status is app.status
        assert app.ctx.header is app.header
        assert app.ctx.theme is app.theme
        assert app.ctx.conversation is app.conversation
        assert app.ctx.ui is app.ui

    def test_the_terminal_contributes_its_own_keys_and_status(self, tmp_path):
        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)

        assert app.keys.running("c-b") is not None
        assert app.keys.running("c-o") is not None

        bar = app.status.toolbar()
        assert "test-model" in bar  # the model segment
        from mocode.cli.app import _shorten_home

        assert _shorten_home(app.cwd) in bar  # the cwd segment, home contracted

    def test_a_plugin_registers_keys_and_middleware(self, tmp_path):
        plugins = tmp_path / "plugins"
        _install(
            plugins,
            cli="""
            from mocode.cli import CLIPlugin

            class InputPlugin(CLIPlugin):
                name = "input.cli"

                def build(self, ctx):
                    ctx.keys.add("c-x", lambda kctx: None, when="running")
                    ctx.input.use(lambda text: text + "!")
        """,
        )
        app = _app(tmp_path, plugins)

        assert app.keys.running("c-x") is not None
        assert app.input_middleware.run("hello") == "hello!"

    def test_a_plugin_can_decorate_header_and_status(self, tmp_path, capsys):
        plugins = tmp_path / "plugins"
        _install(
            plugins,
            cli="""
            from mocode.cli import CLIPlugin
            from mocode.cli.plugin import Segment

            class ChromePlugin(CLIPlugin):
                name = "chrome.cli"

                def build(self, ctx):
                    ctx.header.set(["banner line"])
                    ctx.status.use(lambda s: Segment("[chrome]", priority=99))
        """,
        )
        app = _app(tmp_path, plugins)

        assert app.header.lines == ["banner line"]
        app.display.live = True  # a terminal, not the test pipe: the banner prints
        app._flush_header()
        assert "banner line" in capsys.readouterr().out
        assert app.status.toolbar().startswith("[chrome]")

    @pytest.mark.asyncio
    async def test_on_close_runs_last_registered_first_and_isolated(self, tmp_path):
        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)
        calls = []

        app.ctx.on_close(lambda: calls.append("first"))
        app.ctx.on_close(lambda: calls.append("second") or (_ for _ in ()).throw(
            RuntimeError("boom")
        ))
        app.ctx.on_close(lambda: calls.append("third"))

        await app.ctx.aclose()

        # LIFO; the boom ran inside its own callback, and cost only itself.
        assert calls == ["third", "second", "first"]
        await app.ctx.aclose()  # a second close has nothing left to run

    @pytest.mark.asyncio
    async def test_on_close_awaits_async_callbacks(self, tmp_path):
        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)
        calls = []

        async def _release():
            calls.append("async")

        app.ctx.on_close(_release)
        await app.ctx.aclose()

        assert calls == ["async"]


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


class TestDrawers:
    """The drawer table: how a plugin shapes what the terminal shows."""

    @staticmethod
    def _draw_plugin(app):
        return next(p for p in app.plugins if p.name == "acme.draw")

    def test_a_drawer_registered_from_a_cli_plugin_renders(self, tmp_path, capsys):
        """A plugin's own event type, drawn its own way — not via summary()."""
        plugins = tmp_path / "plugins"
        _install(plugins, cli=DRAWER_CODE)
        app = _app(tmp_path, plugins)

        app.renderer.draw(self._draw_plugin(app).event(score=3))

        assert strip_ansi(capsys.readouterr().out) == "score 3\n"

    def test_a_kind_drawer_shapes_a_plugin_message(self, tmp_path, capsys):
        """Plugin messages are addressed by kind: the block a drawer renders."""
        plugins = tmp_path / "plugins"
        _install(plugins, cli=DRAWER_CODE)
        app = _app(tmp_path, plugins)

        app.renderer.draw(PluginMessage(kind="acme/progress", data={"done": 12}))

        assert strip_ansi(capsys.readouterr().out) == "12 done\n"

    def test_an_event_without_a_drawer_falls_back_to_its_summary(self, tmp_path, capsys):
        """Unregistered still draws — the event describes itself."""
        plugins = tmp_path / "plugins"
        _install(plugins, cli=DRAWER_CODE)
        app = _app(tmp_path, plugins)

        app.renderer.draw(self._draw_plugin(app).event(score=9))
        app.renderer.draw(PluginMessage(kind="acme/unknown-kind", data={}))

        out = strip_ansi(capsys.readouterr().out).splitlines()
        assert out[0] == "score 9"
        assert out[1] == "plugin message: acme/unknown-kind"

    def test_replacing_a_registered_drawer_needs_override(self):
        """Re-skinning a built-in is deliberate, not an accident of load order."""
        from mocode.cli.plugin import DrawerRegistry

        registry = DrawerRegistry()
        with pytest.raises(ValueError):
            registry.register("text", lambda e: [])
        registry.register("text", lambda e: [], override=True)

    def test_the_builtins_are_pre_registered_through_the_same_table(self):
        from mocode.core import Notice, TextDelta
        from mocode.cli import lines
        from mocode.cli.plugin import DrawerRegistry

        registry = DrawerRegistry()

        assert registry.lines_for(Notice(message="careful", level="warn")) == [
            lines.notice("careful", "warn")
        ]
        assert registry.lines_for(TextDelta(text="x")) is None  # no drawer: summary


class TestUI:
    """The runtime channel — said on the stream, asked through the context."""

    @pytest.mark.asyncio
    async def test_ui_message_is_a_notice_on_the_conversation(self, tmp_path, capsys):
        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)
        reader = app.conversation.subscribe()

        await app.ui.message("hello there")

        notice = reader.take()
        assert notice is not None and notice.message == "hello there"
        app.renderer.draw(notice)   # the frontend's half: render what arrived
        assert strip_ansi(capsys.readouterr().out) == "hello there\n"

    def test_the_contexts_ui_is_the_apps_own(self, tmp_path):
        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)

        assert app.ctx.ui is app.ui
        assert app._key_context().ui is app.ui   # key handlers ask through the same one

    @pytest.mark.asyncio
    async def test_the_dialogs_decline_under_the_test_pipe(self, tmp_path):
        """The pipe contract, through the context a plugin actually builds against."""
        plugins = tmp_path / "plugins"
        app = _app(tmp_path, plugins)

        assert app.ctx.ui.is_interactive is False
        assert await app.ctx.ui.confirm("allow?") is False
        assert await app.ctx.ui.select("pick", []) is None
        assert await app.ctx.ui.input("name?") is None

    def test_a_cli_plugin_reaches_the_dialogs_through_the_context(self, tmp_path):
        """The channel a plugin builds against carries the full surface."""
        plugins = tmp_path / "plugins"
        _install(
            plugins,
            cli="""
            from mocode.cli import CLIPlugin

            class AskingPlugin(CLIPlugin):
                name = "asking.cli"

                def build(self, ctx):
                    self.ui = ctx.ui
        """,
        )
        app = _app(tmp_path, plugins)
        plugin = next(p for p in app.plugins if p.name == "asking.cli")

        for method in ("message", "confirm", "select", "input"):
            assert callable(getattr(plugin.ui, method))
        assert plugin.ui.is_interactive is False

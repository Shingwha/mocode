"""The runtime UI channel — three dialogs, each with three paths.

The inline path is driven two ways here: straight through ``UI.feed_key``
(the unit view) and through the app's raw running-time key loop fed by a
prompt_toolkit pipe input (the integration view — the same loop a real
terminal runs). The idle path questionary is replaced with fakes, and the
non-interactive contract is asserted exactly as a pipe would see it. The
command menu and the header's dirty flag ride along: both are terminal
behaviour, and this is the terminal test home.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from mocode.cli.plugin import Option, UI

from .conftest import make_config, strip_ansi

if sys.platform == "win32":
    from prompt_toolkit.input.win32_pipe import Win32PipeInput as _PipeInput
else:
    from prompt_toolkit.input.posix_pipe import PosixPipeInput as _PipeInput


def _ui(drawn: list, *, interactive: bool = True, running: bool = False) -> UI:
    return UI(
        object(),
        is_interactive=interactive,
        chrome=drawn.append,
        running=lambda: running,
    )


def _make_app(tmp_path: Path):
    from mocode.cli import CLIApp

    plugins = tmp_path / "plugins"
    plugins.mkdir(exist_ok=True)
    return CLIApp(
        config=make_config(),
        home=tmp_path / "home",
        interactive=True,
        plugin_dirs=[plugins],
    )


class TestTheNonInteractiveContract:
    """A pipe has no one to ask: the decline answers, and nothing is drawn."""

    @pytest.mark.asyncio
    async def test_confirm_declines(self):
        drawn = []
        ui = _ui(drawn, interactive=False)
        assert await ui.confirm("allow?", danger=True) is False
        assert drawn == []

    @pytest.mark.asyncio
    async def test_select_and_input_come_back_empty(self):
        ui = _ui([], interactive=False)
        assert await ui.select("pick", [Option("a", 1)]) is None
        assert await ui.input("name") is None

    @pytest.mark.asyncio
    async def test_empty_options_pick_none_without_asking(self):
        ui = _ui([], running=True)
        assert await ui.select("pick", []) is None


class TestInlineConfirm:
    @pytest.mark.asyncio
    async def test_yes_no_and_escape(self):
        drawn = []
        ui = _ui(drawn, running=True)

        yes = asyncio.ensure_future(ui.confirm("allow?"))
        await asyncio.sleep(0)
        ui.feed_key("y")
        assert await yes is True

        no = asyncio.ensure_future(ui.confirm("allow?"))
        await asyncio.sleep(0)
        ui.feed_key("n")
        assert await no is False

        esc = asyncio.ensure_future(ui.confirm("allow?"))
        await asyncio.sleep(0)
        ui.feed_key("escape")
        assert await esc is False

        texts = [line.text for line in drawn]
        assert texts[0] == "? allow? [y/n]"          # the question
        assert texts[1] == "allow? — yes"            # each answer stays on screen
        assert texts[3] == "allow? — no"
        assert texts[5] == "allow? — cancelled"

    @pytest.mark.asyncio
    async def test_danger_marks_the_question_red(self):
        drawn = []
        ui = _ui(drawn, running=True)
        ask = asyncio.ensure_future(ui.confirm("delete everything?", danger=True))
        await asyncio.sleep(0)
        ui.feed_key("escape")
        await ask
        assert drawn[0].style == "error" and drawn[0].icon == "!"

    @pytest.mark.asyncio
    async def test_an_open_dialog_swallows_every_key(self):
        drawn = []
        ui = _ui(drawn, running=True)
        ask = asyncio.ensure_future(ui.confirm("allow?"))
        await asyncio.sleep(0)
        assert ui.feed_key("c-b") is True    # a running binding, owned for now
        assert ui.feed_key("f9") is True     # no meaning at all, still swallowed
        assert not ask.done()
        ui.feed_key("y")
        assert await ask is True
        assert ui.feed_key("y") is False     # no dialog open: keys pass through


class TestInlineSelect:
    @pytest.mark.asyncio
    async def test_arrows_choose_and_enter_confirms(self):
        drawn = []
        ui = _ui(drawn, running=True)
        options = [Option("alpha", 1, "first"), Option("beta", 2, "second")]
        ask = asyncio.ensure_future(ui.select("pick", options))
        await asyncio.sleep(0)
        ui.feed_key("down")
        ui.feed_key("enter")
        chosen = await ask
        assert chosen.value == 2
        texts = [line.text for line in drawn]
        assert texts[0] == "? pick"
        assert texts[1] == "  alpha" and drawn[1].note == " first"
        assert texts[2] == "  beta"
        assert "↑↓ navigate" in texts[3]
        assert texts[4] == "❯ alpha"   # the cursor paints, then moves
        assert texts[5] == "❯ beta"
        assert texts[6] == "pick: beta"

    @pytest.mark.asyncio
    async def test_the_cursor_wraps_around(self):
        ui = _ui([], running=True)
        options = [Option("a", 1), Option("b", 2), Option("c", 3)]
        ask = asyncio.ensure_future(ui.select("pick", options))
        await asyncio.sleep(0)
        ui.feed_key("up")     # before the first: wrap to the last
        ui.feed_key("enter")
        assert (await ask).value == 3

    @pytest.mark.asyncio
    async def test_escape_cancels(self):
        drawn = []
        ui = _ui(drawn, running=True)
        ask = asyncio.ensure_future(ui.select("pick", [Option("a", 1)]))
        await asyncio.sleep(0)
        ui.feed_key("escape")
        assert await ask is None
        assert drawn[-1].text == "? pick — cancelled"


class TestInlineInput:
    @pytest.mark.asyncio
    async def test_typing_backspace_and_enter(self):
        ui = _ui([], running=True)
        ask = asyncio.ensure_future(ui.input("name"))
        await asyncio.sleep(0)
        for ch in "hx":
            ui.feed_key(ch)
        ui.feed_key("c-h")        # backspace, raw-loop name
        ui.feed_key("i")
        ui.feed_key("c-m")        # enter, raw-loop name
        assert await ask == "hi"

    @pytest.mark.asyncio
    async def test_enter_on_empty_takes_the_default(self):
        ui = _ui([], running=True)
        ask = asyncio.ensure_future(ui.input("name", default="anon"))
        await asyncio.sleep(0)
        ui.feed_key("enter")
        assert await ask == "anon"

    @pytest.mark.asyncio
    async def test_escape_cancels(self):
        ui = _ui([], running=True)
        ask = asyncio.ensure_future(ui.input("name", default="anon"))
        await asyncio.sleep(0)
        ui.feed_key("escape")
        assert await ask is None


class TestOneQuestionAtATime:
    @pytest.mark.asyncio
    async def test_a_second_dialog_waits_its_turn(self):
        ui = _ui([], running=True)
        first = asyncio.ensure_future(ui.confirm("first?"))
        await asyncio.sleep(0)
        second = asyncio.ensure_future(ui.confirm("second?"))
        await asyncio.sleep(0)
        assert not second.done()      # queued behind the open question
        ui.feed_key("y")
        assert await first is True
        await asyncio.sleep(0)
        assert not second.done()      # still closed until its own answer lands
        ui.feed_key("n")
        assert await second is False


class TestTheIdlePath:
    """At the prompt the questions go to questionary — faked here, mapping asserted."""

    @pytest.mark.asyncio
    async def test_confirm_goes_to_questionary(self, monkeypatch):
        seen = {}

        async def fake_confirm(message, *, danger=False):
            seen.update(message=message, danger=danger)
            return True

        monkeypatch.setattr("mocode.cli.dialogs.confirm", fake_confirm)
        ui = _ui([])   # running=False: the idle path
        assert await ui.confirm("allow?", danger=True) is True
        assert seen == {"message": "allow?", "danger": True}

    @pytest.mark.asyncio
    async def test_select_maps_options_to_choices(self, monkeypatch):
        seen = {}

        async def fake_select(title, choices):
            seen["title"] = title
            seen["choices"] = choices
            return choices[1].value

        monkeypatch.setattr("mocode.cli.dialogs.select", fake_select)
        from mocode.cli import dialogs

        options = [Option("alpha", 1, "first"), Option("beta", 2)]
        ui = _ui([])
        chosen = await ui.select("pick", options)
        assert chosen is options[1]          # the Option rides as the value
        assert isinstance(seen["choices"][0], dialogs.Choice)
        assert seen["choices"][0].title == "alpha"
        assert seen["choices"][0].description == "first"
        assert seen["choices"][1].description is None

    @pytest.mark.asyncio
    async def test_input_goes_to_questionary(self, monkeypatch):
        seen = {}

        async def fake_text(message, *, default=""):
            seen.update(message=message, default=default)
            return "typed"

        monkeypatch.setattr("mocode.cli.dialogs.text", fake_text)
        ui = _ui([])
        assert await ui.input("name", default="anon") == "typed"
        assert seen == {"message": "name", "default": "anon"}


class _Turn:
    """What the running-key loop needs of a turn."""

    def __init__(self):
        self.cancels = 0
        self.cancelled = asyncio.Event()

    def cancel(self):
        self.cancels += 1
        self.cancelled.set()


class TestKeysThroughTheRawLoop:
    """The integration view: pipe input through the app's real raw key loop."""

    @pytest.mark.asyncio
    async def test_a_dialog_answers_from_the_running_key_loop(self, tmp_path, capsys):
        app = _make_app(tmp_path)
        app.ui.is_interactive = True   # the test pipe is not a terminal
        app._running = True            # a turn is in flight
        turn = _Turn()
        with _PipeInput.create() as pipe:
            keys = asyncio.ensure_future(app._running_keys(turn, source=pipe))
            try:
                await asyncio.sleep(0.2)  # let the loop attach
                ask = asyncio.ensure_future(app.ui.confirm("allow rm?", danger=True))
                await asyncio.sleep(0.2)
                pipe.send_text("y")
                assert await asyncio.wait_for(ask, 5) is True
                assert turn.cancels == 0
            finally:
                keys.cancel()
                await asyncio.gather(keys, return_exceptions=True)
        out = strip_ansi(capsys.readouterr().out)
        assert "? allow rm? [y/n]" in out
        assert "allow rm? — yes" in out

    @pytest.mark.asyncio
    async def test_escape_cancels_the_dialog_not_the_turn(self, tmp_path):
        app = _make_app(tmp_path)
        app.ui.is_interactive = True
        app._running = True
        turn = _Turn()
        with _PipeInput.create() as pipe:
            keys = asyncio.ensure_future(app._running_keys(turn, source=pipe))
            try:
                await asyncio.sleep(0.2)
                ask = asyncio.ensure_future(app.ui.confirm("allow?"))
                await asyncio.sleep(0.2)
                pipe.send_text("\x1b")   # a lone escape flushes after the timer
                assert await asyncio.wait_for(ask, 5) is False
                assert turn.cancels == 0   # the dialog took the key
            finally:
                keys.cancel()
                await asyncio.gather(keys, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_a_bound_key_is_swallowed_while_a_dialog_is_open(self, tmp_path):
        app = _make_app(tmp_path)
        app.ui.is_interactive = True
        app._running = True
        turn = _Turn()
        verbose_before = app.renderer._painter.verbose
        with _PipeInput.create() as pipe:
            keys = asyncio.ensure_future(app._running_keys(turn, source=pipe))
            try:
                await asyncio.sleep(0.2)
                ask = asyncio.ensure_future(app.ui.confirm("allow?"))
                await asyncio.sleep(0.2)
                pipe.send_text("\x0f")   # c-o toggles verbose — unless a dialog owns the key
                await asyncio.sleep(0.3)
                assert app.renderer._painter.verbose is verbose_before
                pipe.send_text("y")
                assert await asyncio.wait_for(ask, 5) is True
            finally:
                keys.cancel()
                await asyncio.gather(keys, return_exceptions=True)


class TestTheCommandMenu:
    @pytest.mark.asyncio
    async def test_bare_slash_opens_the_menu_and_refills_the_pick(self, tmp_path):
        from mocode.host.command import CONTINUE

        app = _make_app(tmp_path)
        seen = {}

        async def fake_select(title, options):
            seen["title"] = title
            seen["options"] = options
            return Option("/copy", "/copy", "Copy the last assistant response")

        app.ui.select = fake_select
        result = await app._dispatch("/")
        assert result is CONTINUE               # picked, not executed
        assert seen["title"] == "Select a command:"
        assert app._take_prefill() == "/copy"   # waiting at the next prompt
        assert app._take_prefill() == ""        # once only

    @pytest.mark.asyncio
    async def test_slash_question_opens_it_too(self, tmp_path):
        from mocode.host.command import CONTINUE

        app = _make_app(tmp_path)

        async def cancel_select(title, options):
            return None

        app.ui.select = cancel_select
        assert await app._dispatch("/?") is CONTINUE
        assert app._take_prefill() == ""

    @pytest.mark.asyncio
    async def test_every_registered_command_is_on_the_menu(self, tmp_path):
        app = _make_app(tmp_path)
        seen = {}

        async def fake_select(title, options):
            seen["options"] = options
            return None

        app.ui.select = fake_select
        await app._dispatch("/")
        by_name = {o.label: o for o in seen["options"]}
        # host built-ins, the terminal's own, nothing invented:
        for name in ("/clear", "/export", "/help", "/model", "/quit", "/resume"):
            assert name in by_name
        assert by_name["/model"].description == "Switch provider and model"

    @pytest.mark.asyncio
    async def test_a_pipe_menu_is_a_quiet_noop(self, tmp_path):
        from mocode.host.command import CONTINUE

        app = _make_app(tmp_path)   # ui.is_interactive is False under the test pipe
        assert await app._dispatch("/") is CONTINUE
        assert app._take_prefill() == ""


class TestHeaderPrinting:
    def test_set_raises_the_flag_and_flush_prints_above_the_prompt(
        self, tmp_path, capsys
    ):
        app = _make_app(tmp_path)
        app.header.set(["one banner", "two banner"])
        assert app.header.dirty
        app.display.live = True   # a terminal, not the test pipe
        app._flush_header()
        assert not app.header.dirty
        out = capsys.readouterr().out.splitlines()
        assert out == ["one banner", "two banner"]

    def test_a_pipe_remembers_but_never_prints(self, tmp_path, capsys):
        app = _make_app(tmp_path)
        app.header.set(["quiet banner"])
        app._flush_header()       # live is False under the test pipe
        assert capsys.readouterr().out == ""
        assert app.header.lines == ["quiet banner"]   # the lines stay recorded

    def test_an_empty_set_clears_the_lines(self, tmp_path):
        app = _make_app(tmp_path)
        app.header.set(["banner"])
        app._flush_header()
        app.header.set([])
        assert app.header.lines == []

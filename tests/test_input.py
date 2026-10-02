"""The input layer — middleware chain, keybindings, the status bar, running keys."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mocode.cli.input import Input, build_keybindings
from mocode.cli.plugin import (
    InputMiddleware,
    KeyContext,
    KeyRegistry,
    Segment,
    StatusRegistry,
    StatusState,
)

from .conftest import make_config

if sys.platform == "win32":
    from prompt_toolkit.input.win32_pipe import Win32PipeInput as _PipeInput
else:
    from prompt_toolkit.input.posix_pipe import PosixPipeInput as _PipeInput


def _input(**kwargs) -> Input:
    from mocode.host.command import CommandRegistry

    return Input(CommandRegistry(), **kwargs)


@pytest.fixture
def headless_prompt():
    """A prompt_toolkit app session that never touches a real console."""
    from prompt_toolkit.application.current import create_app_session
    from prompt_toolkit.input.base import DummyInput
    from prompt_toolkit.output.base import DummyOutput

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        yield


def _make_app(tmp_path):
    from mocode.cli import CLIApp

    plugins = tmp_path / "plugins"
    plugins.mkdir(exist_ok=True)
    return CLIApp(
        config=make_config(),
        home=tmp_path / "home",
        interactive=True,
        plugin_dirs=[plugins],
    )


class _FakeSession:
    """Answers prompt_async without a terminal."""

    def __init__(self, text):
        self.text = text

    async def prompt_async(self, *args, **kwargs):
        return self.text


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_the_chain_runs_in_registration_order(self):
        mw = InputMiddleware()
        mw.use(lambda t: t.strip())
        mw.use(lambda t: t.upper())
        inp = _input(middleware=mw)
        inp._session = _FakeSession("  hello  ")

        assert await inp.prompt() == "HELLO"

    @pytest.mark.asyncio
    async def test_none_consumes_the_line(self):
        mw = InputMiddleware()
        mw.use(lambda t: None if t.startswith("/secret") else t)
        inp = _input(middleware=mw)
        inp._session = _FakeSession("/secret stuff")

        assert await inp.prompt() == ""  # consumed: no command, no model call

    @pytest.mark.asyncio
    async def test_passthrough_leaves_the_line_alone(self):
        inp = _input(middleware=InputMiddleware())
        inp._session = _FakeSession("  keep me  ")

        assert await inp.prompt() == "keep me"


class TestIdleKeys:
    def test_registered_idle_keys_join_the_session(self, headless_prompt):
        keys = KeyRegistry()
        seen = []

        def handler(kctx):
            seen.append(kctx.buffer_text)
            return "clear"

        keys.add("c-g", handler, description="grab")
        inp = _input(keys=keys, key_context=lambda buffer=None: KeyContext(
            conversation=None, ui=None, buffer=buffer
        ))
        inp._ensure_session()

        bindings = inp._session.app.key_bindings.get_bindings_for_keys(("c-g",))
        assert len(bindings) == 1

        from prompt_toolkit.buffer import Buffer

        buf = Buffer()
        buf.insert_text("draft")
        bindings[0].call(SimpleNamespace(current_buffer=buf, app=MagicMock()))

        assert seen == ["draft"]
        assert buf.text == ""  # "clear" emptied the buffer

    def test_the_session_merges_plugin_keys_over_prompt_defaults(self, headless_prompt):
        """Ours are prepended, so a registered key beats a prompt default."""
        keys = KeyRegistry()
        keys.add("c-g", lambda kctx: None)
        inp = _input(keys=keys, key_context=lambda buffer=None: None)
        inp._ensure_session()

        merged = inp._session.app.key_bindings.get_bindings_for_keys(("c-c",))
        assert merged[0].handler.__name__ == "_on_ctrl_c"
        # And the prompt's own defaults are still reachable behind ours.
        assert len(merged) > 1


class TestIdleCtrlC:
    @pytest.fixture
    def bound(self, headless_prompt):
        inp = _input()
        inp._ensure_session()
        binding = inp._session.app.key_bindings.get_bindings_for_keys(("c-c",))[0]
        return inp, binding

    @staticmethod
    def _press(binding, text):
        from unittest.mock import MagicMock

        from prompt_toolkit.buffer import Buffer

        buf = Buffer()
        buf.insert_text(text)
        event = SimpleNamespace(current_buffer=buf, app=MagicMock())
        binding.call(event)
        return buf, event

    def test_ctrl_c_clears_text_without_exiting(self, bound):
        inp, binding = bound
        buf, event = self._press(binding, "half a command")

        assert buf.text == ""
        assert not inp.confirm_armed
        event.app.exit.assert_not_called()

    def test_ctrl_c_arms_then_exits_on_empty_input(self, bound):
        inp, binding = bound
        buf, event = self._press(binding, "")
        assert inp.confirm_armed
        event.app.exit.assert_not_called()

        # The second press, still empty, raises KeyboardInterrupt out of the prompt.
        second, event2 = self._press(binding, "")
        assert not inp.confirm_armed
        event2.app.exit.assert_called_once()
        assert event2.app.exit.call_args.kwargs["exception"] is KeyboardInterrupt

    def test_typing_after_arming_disarms(self, bound):
        inp, binding = bound
        self._press(binding, "")
        assert inp.confirm_armed
        buf, _ = self._press(binding, "new text")
        assert not inp.confirm_armed
        assert buf.text == ""

    def test_a_fresh_prompt_disarms(self, bound):
        inp, binding = bound
        self._press(binding, "")
        assert inp.confirm_armed
        inp._confirm_armed = False
        assert not inp.confirm_armed


class TestStatusRegistry:
    @staticmethod
    def _state(**kw):
        kw.setdefault("model", "test-model")
        return StatusState(cwd=Path("/x"), **kw)

    def test_segments_merge_by_priority_with_separator(self):
        reg = StatusRegistry()
        reg.use(lambda s: Segment("low", priority=1))
        reg.use(lambda s: Segment("high", priority=30))
        reg.use(lambda s: Segment("mid", priority=10))

        assert reg.render(self._state(), 80) == "high · mid · low"

    def test_none_and_empty_segments_leave_no_trace(self):
        reg = StatusRegistry()
        reg.use(lambda s: None)
        reg.use(lambda s: Segment(""))
        reg.use(lambda s: Segment("kept"))

        assert reg.render(self._state(), 80) == "kept"

    def test_overwide_lines_truncate_on_the_right(self):
        reg = StatusRegistry()
        reg.use(lambda s: Segment("aaaa", priority=30))
        reg.use(lambda s: Segment("bbbb", priority=20))
        reg.use(lambda s: Segment("cccc", priority=10))

        assert reg.render(self._state(), 12) == "aaaa · bbbb"

    def test_a_single_overwide_segment_gets_ellipsized(self):
        reg = StatusRegistry()
        reg.use(lambda s: Segment("abcdefghij"))

        assert reg.render(self._state(), 6) == "abcde…"

    def test_no_providers_means_an_empty_bar(self):
        assert StatusRegistry().render(self._state(), 80) == ""

    def test_toolbar_renders_from_the_bound_state(self):
        reg = StatusRegistry(state_fn=lambda: self._state(model="from-fn"))
        reg.use(lambda s: Segment(s.model))

        assert reg.toolbar().startswith("from-fn")


class TestRunningKeys:
    class _Turn:
        def __init__(self):
            self.cancels = 0
            self.cancelled = asyncio.Event()

        def cancel(self):
            self.cancels += 1
            self.cancelled.set()

    @pytest.mark.asyncio
    async def test_esc_cancels_the_turn(self, tmp_path):
        app = _make_app(tmp_path)
        turn = self._Turn()
        with _PipeInput.create() as pipe:
            task = asyncio.ensure_future(app._running_keys(turn, source=pipe))
            try:
                await asyncio.sleep(0.2)  # let the loop attach
                pipe.send_text("\x1b")
                await asyncio.wait_for(turn.cancelled.wait(), 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert turn.cancels >= 1

    @pytest.mark.asyncio
    async def test_ctrl_c_cancels_the_turn_too(self, tmp_path):
        app = _make_app(tmp_path)
        turn = self._Turn()
        with _PipeInput.create() as pipe:
            task = asyncio.ensure_future(app._running_keys(turn, source=pipe))
            try:
                await asyncio.sleep(0.2)
                pipe.send_text("\x03")
                await asyncio.wait_for(turn.cancelled.wait(), 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert turn.cancels >= 1

    @pytest.mark.asyncio
    async def test_ctrl_o_flips_verbose_mid_run(self, tmp_path):
        app = _make_app(tmp_path)
        turn = self._Turn()
        painter = app.renderer._painter
        assert painter.verbose is False
        with _PipeInput.create() as pipe:
            task = asyncio.ensure_future(app._running_keys(turn, source=pipe))
            try:
                await asyncio.sleep(0.2)
                pipe.send_text("\x0f")
                for _ in range(100):
                    if painter.verbose:
                        break
                    await asyncio.sleep(0.05)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert painter.verbose is True

    @pytest.mark.asyncio
    async def test_ctrl_b_promotes_the_foreground_command(self, tmp_path):
        app = _make_app(tmp_path)

        class Session:
            async def promote(self):
                return {"shell_id": "shell_1", "status": "running", "promoted": True}

        fake = SimpleNamespace(session=Session())
        app.conversation.tools.get = lambda name: fake if name == "bash" else None
        reader = app.conversation.subscribe()

        await app._dispatch_running_key("c-b", self._Turn())

        notice = reader.take()
        assert notice is not None
        assert notice.message == "moved to background as shell_1"

    @pytest.mark.asyncio
    async def test_ctrl_b_stays_quiet_with_nothing_to_move(self, tmp_path):
        from mocode.core.tool import ToolError

        app = _make_app(tmp_path)

        class Session:
            async def promote(self):
                raise ToolError("no foreground command is running to promote",
                                code="no_running_call")

        fake = SimpleNamespace(session=Session())
        app.conversation.tools.get = lambda name: fake if name == "bash" else None
        reader = app.conversation.subscribe()

        await app._dispatch_running_key("c-b", self._Turn())

        assert reader.take() is None  # silence, by design

    @pytest.mark.asyncio
    async def test_ctrl_b_stays_quiet_without_a_shell(self, tmp_path):
        app = _make_app(tmp_path)
        app.conversation.tools.get = lambda name: None
        reader = app.conversation.subscribe()

        await app._dispatch_running_key("c-b", self._Turn())

        assert reader.take() is None

    @pytest.mark.asyncio
    async def test_an_unbound_key_does_nothing(self, tmp_path):
        app = _make_app(tmp_path)
        turn = self._Turn()

        await app._dispatch_running_key("f9", turn)

        assert turn.cancels == 0


class TestBuildKeybindings:
    def test_extra_bindings_are_appended(self):
        hits = []
        kb = build_keybindings(lambda e: None, extra=[("c-x", lambda e: hits.append("x"))])

        binding = kb.get_bindings_for_keys(("c-x",))[0]
        event = SimpleNamespace(app=MagicMock())
        binding.call(event)
        assert hits == ["x"]


class TestShortenHome:
    def test_home_itself_and_children_contract(self, monkeypatch):
        from pathlib import Path

        from mocode.cli.app import _shorten_home

        home = Path("C:/Users/someone")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert _shorten_home(home) == "~"
        assert _shorten_home(home / "project") == "~/project"

    def test_paths_outside_home_stay_verbatim(self, monkeypatch):
        from pathlib import Path

        from mocode.cli.app import _shorten_home

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("C:/Users/x")))
        assert _shorten_home(Path("D:/elsewhere")) == str(Path("D:/elsewhere"))

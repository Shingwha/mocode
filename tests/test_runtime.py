"""The runtime, and the terminal that is one runtime plus a REPL."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mocode.core.events import RunFinished, TextDelta
from mocode.core.provider import Response, Usage
from mocode.host.runtime import MoCode

from .conftest import make_config, strip_ansi
from .providers import MockProvider

class TestThePackage:
    def test_importing_mocode_stays_lazy(self):
        """``import mocode`` must not drag the host layer in with it.

        PEP 562 is the contract (AGENTS.md: under a millisecond); a subprocess
        checks it on a pristine interpreter, because the first access caches
        the name and in-process order would be lies.
        """
        import subprocess
        import sys

        code = (
            "import mocode\n"
            "assert 'MoCode' not in vars(mocode), 'eagerly imported'\n"
            "assert mocode.MoCode is not None  # …yet resolves on demand\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert done.returncode == 0, done.stderr


class TestTheRuntime:
    def test_a_missing_config_is_reported_clearly(self, tmp_path: Path):
        with patch("mocode.host.runtime.Config.load", return_value=None):
            with pytest.raises(ValueError, match="config.json"):
                MoCode(home=tmp_path / "home", plugin_dirs=[])

    def test_everything_it_owns_lives_under_home(self, tmp_path: Path):
        """Nothing the runtime writes escapes its home — not even sessions."""
        mc = MoCode(config=make_config(), home=tmp_path / "home", plugin_dirs=[])
        mc.config.save = lambda *a, **k: None

        assert mc.home == tmp_path / "home"
        assert mc.store._base_dir == tmp_path / "home" / "sessions"

    def test_it_holds_no_conversation(self, mc: MoCode):
        """Opening is the runtime's job; being one is not."""
        assert not hasattr(mc, "chat")
        assert not hasattr(mc, "messages")
        assert not hasattr(mc, "agent")

    def test_opening_a_conversation_does_not_write_anything(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        assert conversation.id.startswith("session_")
        assert mc.store.list_all() == []


class TestAConversation:
    @pytest.mark.asyncio
    async def test_streams_events_and_answers(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        conversation.agent.provider = MockProvider(
            [Response(content="hi there", usage=Usage(2, 3), finish_reason="stop")],
            chunk_size=2,
        )

        events = [event async for event in conversation.stream("hello")]

        assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "hi there"
        final = events[-1]
        assert isinstance(final, RunFinished) and final.content == "hi there"
        assert conversation.state.answer == "hi there"
        assert conversation.messages[0] == {"role": "user", "content": "hello"}

    def test_no_display_is_needed_for_any_of_it(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        assert conversation.host.ctx.agent is conversation.agent


class TestTheTerminal:
    """`CLIApp` is a runtime, one conversation, and a terminal in front of it."""

    @pytest.fixture
    def app(self, tmp_path: Path):
        from mocode.cli import CLIApp

        return CLIApp(
            config=make_config(), home=tmp_path / "home",
            interactive=True, plugin_dirs=[],
        )

    def test_its_commands_come_from_its_plugin(self, app):
        assert "/help" in {c.name for c in app.commands.all()}
        assert "cli" in [p.name for p in app.plugins]

    def test_the_renderer_is_installed_not_contributed(self, app, tmp_path: Path):
        """Drawing a terminal is what this frontend does with the event stream."""
        from mocode.cli.render import CLIRenderer

        assert isinstance(app.renderer, CLIRenderer)
        # The renderer is the app's own doing — the host's plugin set (the
        # built-ins, here) contains nothing that installed it.
        assert "cli" not in [p.name for p in app.runtime.plugins_for(tmp_path)]

    def test_it_writes_sessions_under_its_own_home(self, app, tmp_path: Path):
        app.conversation.messages.append({"role": "user", "content": "hi"})
        app.conversation.save()

        assert (tmp_path / "home" / "sessions").is_dir()
        assert app.runtime.store.list_all() != []

    @pytest.mark.asyncio
    async def test_a_turn_is_echoed_and_answered(self, app, capsys):
        """The interactive path end to end — the one a user actually walks into.

        Everything here is reached only by running the REPL, which is exactly
        why a renamed display primitive can otherwise break the app silently.
        """
        app.conversation.agent.provider = MockProvider(
            [Response(content="pong", usage=Usage(1, 1), finish_reason="stop")]
        )
        typed = iter(["ping", "/quit"])

        async def scripted_prompt() -> str:
            return next(typed)

        app.display.prompt = scripted_prompt
        await app._repl()

        out = strip_ansi(capsys.readouterr().out.replace("\r", "\n"))
        rendered = [line.rstrip() for line in out.splitlines() if line.strip()]
        assert rendered[:2] == ["❯ ping", "pong"]
        assert rendered[2] == "↑1 ↓1 tokens"   # what the turn cost
        assert set(rendered[3]) == {"─"}       # the rule closes it before the next prompt

    @pytest.mark.asyncio
    async def test_a_command_speaks_through_the_same_stream(self, app, capsys):
        """`/help` publishes a notice; the renderer draws it like anything else."""
        app.display.clear_screen = lambda: None
        typed = iter(["/help", "/quit"])

        async def scripted_prompt() -> str:
            return next(typed)

        app.display.prompt = scripted_prompt
        await app._repl()

        out = strip_ansi(capsys.readouterr().out)
        assert "/help" in out and "Show available commands" in out

    def test_a_one_shot_can_render_without_a_repl(self, tmp_path: Path):
        """`-p` on a terminal draws the turn; `render` asks for that."""
        from mocode.cli import CLIApp

        app = CLIApp(
            config=make_config(), home=tmp_path / "home",
            interactive=False, render=True,
        )

        assert app.display is not None and app.renderer is not None

    def test_interactive_implies_rendering(self, tmp_path: Path):
        """`render` can only ask for a frontend, never take one away."""
        from mocode.cli import CLIApp

        app = CLIApp(
            config=make_config(), home=tmp_path / "home",
            interactive=True, render=False,
        )

        assert app.display is not None

    def test_a_piped_one_shot_attaches_nothing(self, tmp_path: Path):
        """The default for a non-interactive run — that is what keeps `-p` a pipe."""
        from mocode.cli import CLIApp

        app = CLIApp(config=make_config(), home=tmp_path / "home", interactive=False)

        assert app.display is None
        assert app.renderer is None

    def test_a_headless_cli_still_has_commands(self, tmp_path: Path):
        from mocode.cli import CLIApp

        app = CLIApp(config=make_config(), home=tmp_path / "home", interactive=False)

        assert "/help" in {c.name for c in app.commands.all()}

    def test_a_piped_run_saves_nothing(self, tmp_path: Path, capsys):
        """A one-shot is not a session."""
        from mocode.cli import CLIApp

        app = CLIApp(config=make_config(), home=tmp_path / "home", interactive=False)
        app.conversation.agent.provider = MockProvider(
            [Response(content="answer", usage=Usage(1, 1), finish_reason="stop")]
        )

        app.run_oneshot("hello")

        assert capsys.readouterr().out.strip() == "answer"
        assert app.runtime.store.list_all() == []

    def test_a_rendered_one_shot_prints_nothing_twice(self, tmp_path: Path, capsys):
        from mocode.cli import CLIApp

        app = CLIApp(
            config=make_config(), home=tmp_path / "home",
            interactive=False, render=True,
        )
        app.display.clear_screen = lambda: None
        app.conversation.agent.provider = MockProvider(
            [Response(content="drawn", usage=Usage(1, 1), finish_reason="stop")]
        )

        app.run_oneshot("hello")

        out = strip_ansi(capsys.readouterr().out)
        assert out.count("drawn") == 1

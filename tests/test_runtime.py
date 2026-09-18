"""The headless runtime — assembly, session lifecycle, provider switching."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.plugin.host import builtin_plugins
from mocode.host.runtime import MoCode
from mocode.host.session import Session, SessionStore
from mocode.core.events import RunFinished, TextDelta
from mocode.core.provider import Response, Usage

from .providers import MockProvider

#: Every escape a terminal draw can emit.
ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _config(tmp_path) -> Config:
    return Config(
        active_provider="test",
        active_model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                base_url="http://localhost",
                models={"test-model": ModelEntry()},
            )
        },
    )


@pytest.fixture
def mc(tmp_path, monkeypatch) -> MoCode:
    # Keep the session store inside tmp_path — never the real ~/.mocode.
    monkeypatch.setattr(
        "mocode.host.runtime.SessionStore",
        lambda: SessionStore(base_dir=tmp_path / "sessions"),
    )
    runtime = MoCode(
        config=_config(tmp_path),
        home=tmp_path / "home",
        cwd=tmp_path,
        plugin_dirs=[],
    )
    runtime.config.save = MagicMock()
    return runtime


def _session(messages: list[dict], session_id: str = "session_abc") -> Session:
    return Session(
        id=session_id,
        created_at="2025-01-01T00:00:00",
        updated_at="2025-01-01T00:00:00",
        workdir="/tmp",
        messages=messages,
    )


class TestAssembly:
    def test_builtin_tools_are_available(self, mc: MoCode):
        assert sorted(mc.tools.names()) == ["bash", "edit", "read", "skill", "write"]

    def test_the_host_contributes_no_commands_of_its_own(self, mc: MoCode):
        """`/help` and friends are the terminal's; the host only relays whatever
        plugins contribute, so a headless app starts with an empty registry."""
        assert mc.commands.all() == []

    def test_a_missing_config_is_reported_clearly(self, tmp_path):
        with patch("mocode.host.runtime.Config.load", return_value=None):
            with pytest.raises(ValueError, match="config.json"):
                MoCode(cwd=tmp_path, plugin_dirs=[])

    def test_a_headless_run_has_no_display(self, mc: MoCode):
        assert mc.display is None
        assert mc.ctx.display is None


class TestTheTerminal:
    """The CLI is a MoCode runtime plus its own plugin — nothing more."""

    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        from mocode.cli import CLIApp

        monkeypatch.setattr(
            "mocode.host.runtime.SessionStore",
            lambda: SessionStore(base_dir=tmp_path / "sessions"),
        )
        return CLIApp(config=_config(tmp_path), home=tmp_path / "home", interactive=True)

    def test_the_terminal_commands_come_from_its_plugin(self, app):
        assert "/help" in {c.name for c in app.commands.all()}
        # …which the terminal passed in, not something the host knows about
        assert "cli" in [p.name for p in app.runtime.host.plugins]
        assert "cli" not in [p.name for p in builtin_plugins()]

    def test_the_terminal_renderer_is_installed_as_a_hook(self, app):
        from mocode.cli.hook import CLIDisplayHook

        assert any(isinstance(h, CLIDisplayHook) for h in app.runtime.ctx.hooks)

    @pytest.mark.asyncio
    async def test_a_turn_is_echoed_and_answered(self, app, capsys):
        """The interactive path end to end — the one a user actually walks into.

        Everything here is reached only by running the REPL, which is exactly
        why a renamed display primitive can otherwise break the app silently.
        """
        app.runtime.agent.provider = MockProvider(
            [Response(content="pong", usage=Usage(1, 1), finish_reason="stop")]
        )
        app.display.clear_screen = lambda: None
        typed = iter(["ping", "/quit"])

        async def scripted_prompt() -> str:
            return next(typed)

        app.display.prompt = scripted_prompt
        await app._repl()

        out = ESCAPES.sub("", capsys.readouterr().out.replace("\r", "\n"))
        rendered = [line.rstrip() for line in out.splitlines() if line.strip()]
        assert rendered[:2] == ["❯ ping", "pong"]
        assert set(rendered[2]) == {"─"}  # the turn is ruled off before the next prompt

    def test_a_one_shot_can_render_without_a_repl(self, tmp_path, monkeypatch):
        """`-p` on a terminal draws the turn; `render` asks for that."""
        from mocode.cli import CLIApp
        from mocode.cli.hook import CLIDisplayHook

        monkeypatch.setattr(
            "mocode.host.runtime.SessionStore",
            lambda: SessionStore(base_dir=tmp_path / "sessions"),
        )
        app = CLIApp(
            config=_config(tmp_path), home=tmp_path / "home",
            interactive=False, render=True,
        )

        assert app.display is not None
        assert any(isinstance(h, CLIDisplayHook) for h in app.runtime.ctx.hooks)

    def test_interactive_implies_rendering(self, tmp_path, monkeypatch):
        """`render` can only ask for a frontend, never take one away."""
        from mocode.cli import CLIApp

        monkeypatch.setattr(
            "mocode.host.runtime.SessionStore",
            lambda: SessionStore(base_dir=tmp_path / "sessions"),
        )
        app = CLIApp(
            config=_config(tmp_path), home=tmp_path / "home",
            interactive=True, render=False,
        )

        assert app.display is not None

    def test_a_piped_one_shot_attaches_nothing(self, tmp_path, monkeypatch):
        """The default for a non-interactive run — that is what keeps `-p` a pipe."""
        from mocode.cli import CLIApp

        monkeypatch.setattr(
            "mocode.host.runtime.SessionStore",
            lambda: SessionStore(base_dir=tmp_path / "sessions"),
        )
        app = CLIApp(config=_config(tmp_path), home=tmp_path / "home", interactive=False)

        assert app.display is None

    def test_a_display_less_cli_gets_commands_but_no_renderer(self, tmp_path, monkeypatch):
        from mocode.cli import CLIApp
        from mocode.cli.hook import CLIDisplayHook

        monkeypatch.setattr(
            "mocode.host.runtime.SessionStore",
            lambda: SessionStore(base_dir=tmp_path / "sessions"),
        )
        app = CLIApp(config=_config(tmp_path), home=tmp_path / "home", interactive=False)

        assert "/help" in {c.name for c in app.commands.all()}
        assert not any(isinstance(h, CLIDisplayHook) for h in app.runtime.ctx.hooks)


class TestChat:
    @pytest.mark.asyncio
    async def test_streams_events_and_answers(self, mc: MoCode):
        mc.agent.provider = MockProvider(
            [Response(content="hi there", usage=Usage(2, 3), finish_reason="stop")],
            chunk_size=2,
        )

        events = [event async for event in mc.chat("hello")]

        assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "hi there"
        final = events[-1]
        assert isinstance(final, RunFinished) and final.content == "hi there"
        assert mc.state.answer == "hi there"
        assert mc.messages[0] == {"role": "user", "content": "hello"}


class TestSessionLifecycle:
    def test_save_is_a_noop_without_messages(self, mc: MoCode):
        mc.save_session()
        assert mc.sessions.list() == []

    def test_save_persists_the_conversation(self, mc: MoCode):
        mc.messages.append({"role": "user", "content": "hello"})
        mc.save_session()
        assert mc.sessions.get_active().messages == mc.messages

    def test_using_a_session_saves_the_current_one_first(self, mc: MoCode):
        mc.messages.append({"role": "user", "content": "old"})

        mc.use_session(_session([{"role": "user", "content": "resumed"}]))

        assert [s.messages for s in mc.sessions.list()] == [
            [{"role": "user", "content": "old"}]
        ]
        assert mc.messages == [{"role": "user", "content": "resumed"}]
        assert mc.sessions.active_id == "session_abc"

    def test_starting_a_session_clears_the_conversation(self, mc: MoCode):
        mc.messages.append({"role": "user", "content": "old"})
        mc.start_session()
        assert mc.messages == []
        assert mc.sessions.active_id is not None

    def test_starting_a_session_can_seed_messages(self, mc: MoCode):
        mc.start_session([{"role": "user", "content": "imported"}])
        assert mc.messages == [{"role": "user", "content": "imported"}]

    def test_replacing_messages_rebuilds_the_system_prompt(self, mc: MoCode):
        mc.agent.system_prompt = "stale"
        mc.replace_messages([{"role": "user", "content": "hi"}])
        assert mc.agent.system_prompt != "stale"
        assert "<system-prompt>" in mc.agent.system_prompt


class TestProviderSwitch:
    def test_switch_updates_config_agent_and_model_spec(self, mc: MoCode):
        before = mc.agent.provider

        mc.switch_provider("test", "test-model")

        assert mc.config.active_provider == "test"
        assert mc.config.active_model == "test-model"
        assert mc.agent.provider is not before
        assert mc.ctx.model.name == "test-model"
        assert mc.agent.model is mc.ctx.model
        mc.config.save.assert_called_once()

    def test_unknown_provider_is_reported(self, mc: MoCode):
        with pytest.raises(ValueError, match="not defined"):
            mc.switch_provider("nope", "m")

"""Tests for CLIApp session lifecycle methods."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mocode.app.cli.app import CLIApp
from mocode.app.config import Config, ProviderEntry, ModelEntry
from mocode.app.session import Session, SessionManager


def _make_app() -> CLIApp:
    """Create a CLIApp with mock agent and session manager."""
    config = Config(
        active_provider="test",
        active_model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                models=[ModelEntry(name="test-model")],
            ),
        },
    )
    config.save = MagicMock()

    mock_display = MagicMock()
    mock_session_mgr = MagicMock(spec=SessionManager)
    with (
        patch.object(
            CLIApp,
            "_build_agent",
            return_value=MagicMock(messages=[], system_prompt=""),
        ),
        patch.object(CLIApp, "_build_prompt", return_value="test-prompt"),
        patch("mocode.app.cli.app.SessionManager", return_value=mock_session_mgr),
    ):
        app = CLIApp(config=config, display=mock_display)
    app._session_mgr = mock_session_mgr
    mock_session_mgr.reset_mock()
    app._build_prompt = MagicMock(return_value="test-prompt")
    return app


class TestClearConversation:
    def test_clears_messages(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        app.clear_conversation()

        assert app.agent.messages == []

    def test_saves_session_first(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        app.clear_conversation()

        app._session_mgr.save.assert_called_once()

    def test_clears_screen(self):
        app = _make_app()

        app.clear_conversation()

        app.display.clear_screen.assert_called_once()


class TestResumeFromFile:
    def test_loads_messages(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        new_msgs = [{"role": "user", "content": "new"}]
        app.resume_from_file(new_msgs)

        assert app.agent.messages == new_msgs

    def test_saves_session_first(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        app.resume_from_file([{"role": "user", "content": "new"}])

        app._session_mgr.save.assert_called_once()

    def test_renders_messages(self):
        app = _make_app()
        msgs = [{"role": "user", "content": "hello"}]

        app.resume_from_file(msgs)

        app.display.render_messages.assert_called_once_with(msgs)


class TestResumeSession:
    def test_preserves_session_identity(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        session = Session(
            id="session_abc",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "resumed"}],
        )
        app.resume_session(session)

        assert app.agent.messages == session.messages
        app._session_mgr.switch_to.assert_called_once_with(session)
        app._session_mgr.create.assert_not_called()

    def test_saves_current_before_resume(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        session = Session(
            id="session_abc",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/tmp",
            messages=[],
        )
        app.resume_session(session)

        app._session_mgr.save.assert_called_once()

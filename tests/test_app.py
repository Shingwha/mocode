"""Tests for CLIApp rebuild_agent() and replace_messages()."""

from unittest.mock import MagicMock, patch

from mocode.app.cli.app import CLIApp
from mocode.app.config import Config, ProviderEntry, ModelEntry
from mocode.app.session import SessionManager


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
        patch.object(CLIApp, "_build_agent", return_value=MagicMock(messages=[], system_prompt="")),
        patch.object(CLIApp, "_build_prompt", return_value="test-prompt"),
        patch("mocode.app.cli.app.SessionManager", return_value=mock_session_mgr),
    ):
        app = CLIApp(config=config, display=mock_display)
    app._session_mgr = mock_session_mgr
    # Reset call counts — __init__ already called create()
    mock_session_mgr.reset_mock()
    # Keep _build_prompt mocked on the instance for rebuild/replace calls
    app._build_prompt = MagicMock(return_value="test-prompt")
    return app


class TestRebuildAgent:
    def test_preserves_messages(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "hello"}]

        # Mock _build_agent to return a new mock with empty messages
        new_agent = MagicMock(messages=[], system_prompt="")
        with patch.object(app, "_build_agent", return_value=new_agent):
            app.rebuild_agent()

        assert new_agent.messages == [{"role": "user", "content": "hello"}]

    def test_calls_config_save(self):
        app = _make_app()
        new_agent = MagicMock(messages=[], system_prompt="")
        with patch.object(app, "_build_agent", return_value=new_agent):
            app.rebuild_agent()

        app.config.save.assert_called_once()

    def test_calls_save_session_first(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "test"}]

        new_agent = MagicMock(messages=[], system_prompt="")
        with patch.object(app, "_build_agent", return_value=new_agent):
            app.rebuild_agent()

        app._session_mgr.save.assert_called_once()

    def test_displays_info(self):
        app = _make_app()
        new_agent = MagicMock(messages=[], system_prompt="")
        with patch.object(app, "_build_agent", return_value=new_agent):
            app.rebuild_agent()

        app.display.info.assert_called_once_with("Config saved.")


class TestReplaceMessages:
    def test_clears_and_extends_messages(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        new_msgs = [{"role": "user", "content": "new"}]
        app.replace_messages(new_msgs)

        assert app.agent.messages == new_msgs

    def test_empty_list_clears(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        app.replace_messages([])

        assert app.agent.messages == []

    def test_saves_session_first(self):
        app = _make_app()
        app.agent.messages = [{"role": "user", "content": "old"}]

        app.replace_messages([])

        app._session_mgr.save.assert_called_once()

    def test_clears_and_creates_session(self):
        app = _make_app()

        app.replace_messages([])

        app._session_mgr.clear.assert_called_once()
        app._session_mgr.create.assert_called_once()

    def test_clears_screen(self):
        app = _make_app()

        app.replace_messages([])

        app.display.clear_screen.assert_called_once()

    def test_renders_messages_when_non_empty(self):
        app = _make_app()
        msgs = [{"role": "user", "content": "hello"}]

        app.replace_messages(msgs)

        app.display.render_messages.assert_called_once_with(msgs)

    def test_does_not_render_when_empty(self):
        app = _make_app()

        app.replace_messages([])

        app.display.render_messages.assert_not_called()

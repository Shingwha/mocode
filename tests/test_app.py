"""Tests for CLIApp session lifecycle methods."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mocode.app.cli.app import CLIApp
from mocode.app.cli.display import Display
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


# ── Display.render_messages tests ─────────────────────────


def _make_display(capture: list | None = None):
    """Create a real Display that captures _print output."""
    lines = capture if capture is not None else []
    d = Display(input_=MagicMock())
    d._print = lambda *a, **kw: lines.append(
        " ".join(str(x) for x in a)
    )
    return d


class TestRenderMessages:
    def test_user_message(self):
        captured = []
        d = _make_display(captured)
        d.render_messages([{"role": "user", "content": "hello"}])
        assert any("hello" in line for line in captured)

    def test_assistant_text_response(self):
        captured = []
        d = _make_display(captured)
        d.render_messages([{"role": "assistant", "content": "hi there"}])
        assert any("hi there" in line for line in captured)

    def test_assistant_reasoning(self):
        captured = []
        d = _make_display(captured)
        d.render_messages([
            {"role": "assistant", "content": "answer", "reasoning_content": "thinking..."}
        ])
        assert any("thinking..." in line for line in captured)

    def test_tool_calls_show_done_not_start(self):
        """Tool calls should render as ✓ (tool_done), not ◆ (old tool_start_batched)."""
        captured = []
        d = _make_display(captured)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a.py"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file content here"},
        ]
        d.render_messages(messages)
        # Should contain ✓ (tool_done), not ◆ (tool_start icon)
        tool_lines = [l for l in captured if "read" in l and "a.py" in l]
        assert len(tool_lines) == 1
        assert "✓" in tool_lines[0]
        assert "a.py" in tool_lines[0]

    def test_failed_tool_shows_fail(self):
        """Failed tool results should render as ✗ (tool_fail)."""
        captured = []
        d = _make_display(captured)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"bad"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "error: command not found"},
        ]
        d.render_messages(messages)
        tool_lines = [l for l in captured if "bash" in l]
        assert len(tool_lines) == 1
        assert "✗" in tool_lines[0]
        assert "error: command not found" in tool_lines[0]

    def test_timeout_tool_shows_fail(self):
        """Timeout tool results should render as ✗ (tool_fail)."""
        captured = []
        d = _make_display(captured)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"slow"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "timeout: 240s"},
        ]
        d.render_messages(messages)
        tool_lines = [l for l in captured if "bash" in l]
        assert len(tool_lines) == 1
        assert "✗" in tool_lines[0]

    def test_mixed_success_and_failure(self):
        """Mixed batch: success shows ✓, failure shows ✗."""
        captured = []
        d = _make_display(captured)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"ok.py"}'}},
                    {"id": "call_2", "type": "function", "function": {"name": "bash", "arguments": '{"command":"fail"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
            {"role": "tool", "tool_call_id": "call_2", "content": "error: boom"},
        ]
        d.render_messages(messages)
        tool_lines = [l for l in captured if "✓" in l or "✗" in l]
        assert len(tool_lines) == 2
        assert any("✓" in l and "read" in l for l in tool_lines)
        assert any("✗" in l and "bash" in l for l in tool_lines)

    def test_grouped_tools_batched(self):
        """Multiple calls to same tool are grouped into one line."""
        captured = []
        d = _make_display(captured)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a.py"}'}},
                    {"id": "call_2", "type": "function", "function": {"name": "read", "arguments": '{"path":"b.py"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "content a"},
            {"role": "tool", "tool_call_id": "call_2", "content": "content b"},
        ]
        d.render_messages(messages)
        tool_lines = [l for l in captured if "✓" in l and "read" in l]
        # Grouped into a single line
        assert len(tool_lines) == 1
        assert "a.py" in tool_lines[0]
        assert "b.py" in tool_lines[0]

    def test_tool_messages_not_rendered_raw(self):
        """Tool role messages should not produce raw output lines."""
        captured = []
        d = _make_display(captured)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a.py"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file content here"},
        ]
        d.render_messages(messages)
        # "file content here" should NOT appear as a raw line
        assert not any("file content here" in l for l in captured)

    def test_full_conversation_resume(self):
        """Full conversation with multiple turns renders correctly."""
        captured = []
        d = _make_display(captured)
        messages = [
            {"role": "user", "content": "read the file"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"main.py"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "print('hello')"},
            {"role": "assistant", "content": "The file contains print('hello')."},
        ]
        d.render_messages(messages)
        # User message, tool done line, and final response should all appear
        assert any("read the file" in l for l in captured)
        assert any("✓" in l and "read" in l for l in captured)
        assert any("print('hello')" in l for l in captured)

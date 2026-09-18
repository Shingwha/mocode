"""Tests for mocode.host.session — Session, SessionStore, SessionManager."""

import pytest

from mocode.host.session import (
    Session,
    SessionManager,
    SessionStore,
)


class TestSession:
    def test_create(self):
        s = Session(
            id="s1",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert s.id == "s1"
        assert len(s.messages) == 1
        assert s.title == ""
        assert s.metadata == {}

    def test_to_dict_roundtrip(self):
        s = Session(
            id="s1",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hi"}],
            title="test",
            model="gpt-4o",
            provider="openai",
            metadata={"key": "value"},
        )
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.id == s.id
        assert s2.title == s.title
        assert s2.messages == s.messages
        assert s2.metadata == {"key": "value"}


class TestSessionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return SessionStore(base_dir=tmp_path / "sessions")

    def test_save_and_load(self, store):
        s = Session(
            id="session_abc",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/project",
            messages=[],
        )
        store.save("/project", s)
        loaded = store.load("/project", "session_abc")
        assert loaded is not None
        assert loaded.id == "session_abc"

    def test_list_sorted(self, store):
        s1 = Session(
            id="session_s1",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/p",
            messages=[],
        )
        s2 = Session(
            id="session_s2",
            created_at="2025-01-02T00:00:00",
            updated_at="2025-01-02T00:00:00",
            workdir="/p",
            messages=[],
        )
        store.save("/p", s1)
        store.save("/p", s2)
        sessions = store.list("/p")
        assert len(sessions) == 2
        assert sessions[0].id == "session_s2"  # most recent first

    def test_delete(self, store):
        s = Session(
            id="session_s1",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir="/p",
            messages=[],
        )
        store.save("/p", s)
        assert store.delete("/p", "session_s1") is True
        assert store.load("/p", "session_s1") is None


class TestSessionManager:
    @pytest.fixture
    def manager(self, tmp_path):
        store = SessionStore(base_dir=tmp_path / "sessions")
        return SessionManager(workdir="/project", store=store)

    def test_create(self, manager):
        sid = manager.create()
        assert sid.startswith("session_")
        assert manager.active_id == sid

    def test_save_and_resume(self, manager):
        sid = manager.create()
        messages = [{"role": "user", "content": "hello"}]
        manager.save(messages, model="gpt-4o", provider="openai")

        session = manager.resume(sid)
        assert session is not None
        assert session.messages == messages
        assert session.model == "gpt-4o"

    def test_resume_nonexistent(self, manager):
        assert manager.resume("nope") is None

    def test_switch_to(self, manager):
        sid = manager.create()
        session = manager.resume(sid)
        assert session is not None

        manager.clear()
        assert manager.active_id is None

        manager.switch_to(session)
        assert manager.active_id == sid

    def test_get_active(self, manager):
        assert manager.get_active() is None

        sid = manager.create()
        manager.save([{"role": "user", "content": "hi"}])

        active = manager.get_active()
        assert active is not None
        assert active.id == sid

    def test_export_and_import(self, manager, tmp_path):
        sid = manager.create()
        messages = [{"role": "user", "content": "hello"}]
        manager.save(messages, model="gpt-4o", provider="openai")
        session = manager.get_active()

        path = tmp_path / "export.json"
        manager.export_to_file(session, path, system_prompt="You are helpful.")
        assert path.exists()

        result = SessionManager.import_from_file(path)
        assert result is not None
        imported_msgs, title = result
        assert imported_msgs == messages

    def test_import_nonexistent(self, manager, tmp_path):
        assert SessionManager.import_from_file(tmp_path / "nope.json") is None

    def test_export_to_md_basic(self, manager, tmp_path):
        sid = manager.create()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        manager.save(messages, model="gpt-4o", provider="openai")
        session = manager.get_active()

        path = tmp_path / "export.md"
        manager.export_to_md(session, path, system_prompt="You are helpful.")
        assert path.exists()

        md = path.read_text(encoding="utf-8")
        # Frontmatter
        assert md.startswith("---")
        assert "session_id:" in md
        assert "model: gpt-4o" in md
        # System prompt
        assert "## System Prompt" in md
        assert "You are helpful." in md
        # Content
        assert "## Turn 1" in md
        assert "### User" in md
        assert "hello" in md
        assert "### Assistant" in md
        assert "hi there" in md

    def test_export_to_md_with_tool_calls(self, manager, tmp_path):
        sid = manager.create()
        messages = [
            {"role": "user", "content": "read file"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "test.py"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "print('hello')"},
            {"role": "assistant", "content": "Here is the file content."},
        ]
        manager.save(messages)
        session = manager.get_active()

        path = tmp_path / "export.md"
        manager.export_to_md(session, path, system_prompt="")
        md = path.read_text(encoding="utf-8")

        assert "#### Tool Call: read" in md
        assert "call_1" in md
        assert "Tool Result: read" in md
        assert "print('hello')" in md
        assert "Here is the file content." in md

    def test_export_to_md_with_reasoning(self, manager, tmp_path):
        sid = manager.create()
        messages = [
            {"role": "user", "content": "think about this"},
            {
                "role": "assistant",
                "content": "The answer is 42.",
                "reasoning_content": "Let me think step by step...",
            },
        ]
        manager.save(messages)
        session = manager.get_active()

        path = tmp_path / "export.md"
        manager.export_to_md(session, path)
        md = path.read_text(encoding="utf-8")

        assert "<details><summary>Thinking</summary>" in md
        assert "Let me think step by step..." in md
        assert "The answer is 42." in md

    def test_export_to_md_empty_session(self, manager, tmp_path):
        sid = manager.create()
        session = manager.get_active()

        path = tmp_path / "export.md"
        manager.export_to_md(session, path, system_prompt="Be brief.")
        md = path.read_text(encoding="utf-8")

        assert md.startswith("---")
        assert "## System Prompt" in md
        assert "## Turn" not in md

"""Tests for mocode.app.session — Session, FileSessionStore, SessionManager."""

import pytest

from mocode.app.session import (
    FileSessionStore,
    Session,
    SessionManager,
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


class TestFileSessionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return FileSessionStore(base_dir=tmp_path / "sessions")

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
        store = FileSessionStore(base_dir=tmp_path / "sessions")
        return SessionManager(workdir="/project", store=store)

    def test_create(self, manager):
        sid = manager.create()
        assert sid.startswith("session_")
        assert manager.active_id == sid
        assert manager.is_dirty is False

    def test_save_and_resume(self, manager):
        sid = manager.create()
        messages = [{"role": "user", "content": "hello"}]
        manager.save(messages, model="gpt-4o", provider="openai")

        session = manager.resume(sid)
        assert session is not None
        assert session.messages == messages
        assert session.model == "gpt-4o"

    def test_dirty_tracking(self, manager):
        assert manager.is_dirty is False
        manager.mark_dirty()
        assert manager.is_dirty is True

        manager.create()
        assert manager.is_dirty is False

    def test_resume_nonexistent(self, manager):
        assert manager.resume("nope") is None

"""Session management tests — v0.2 (SessionStore)"""

import pytest

from mocode.store import Session, InMemorySessionStore
from mocode.session import SessionManager


@pytest.fixture
def store():
    return InMemorySessionStore()


@pytest.fixture
def manager(store):
    return SessionManager("/fake/workdir", store=store)


class TestSession:
    def test_from_dict(self):
        data = {
            "id": "session_20260318_143022",
            "created_at": "2026-03-18T14:30:22",
            "updated_at": "2026-03-18T14:30:22",
            "workdir": "/home/user/project",
            "messages": [{"role": "user", "content": "hello"}],
            "model": "gpt-4o",
            "provider": "openai",
        }
        s = Session.from_dict(data)
        assert s.id == "session_20260318_143022"
        assert s.message_count == 1

    def test_to_dict(self):
        s = Session(
            id="s1",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            workdir="/tmp",
            messages=[],
        )
        d = s.to_dict()
        assert d["id"] == "s1"

    def test_roundtrip(self):
        original = Session(
            id="s_rt",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hi"}],
            model="m1",
            provider="p1",
        )
        restored = Session.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.messages == original.messages


class TestSessionManager:
    def test_save_and_load(self, manager):
        messages = [{"role": "user", "content": "hello"}]
        session = manager.save_session(messages, model="gpt-4o", provider="openai")
        assert session.id.startswith("session_")
        assert session.message_count == 1

        loaded = manager.load_session(session.id)
        assert loaded is not None
        assert loaded.messages == messages

    def test_list_sessions_empty(self, manager):
        sessions = manager.list_sessions()
        assert sessions == []

    def test_delete_session(self, manager):
        session = manager.save_session([{"role": "user", "content": "x"}])
        assert manager.delete_session(session.id)
        assert manager.load_session(session.id) is None

    def test_delete_nonexistent(self, manager):
        assert not manager.delete_session("nope")

    def test_load_nonexistent(self, manager):
        assert manager.load_session("nope") is None

    def test_update_session(self, manager):
        session = manager.save_session([{"role": "user", "content": "old"}])
        updated = manager.update_session(session.id, [{"role": "user", "content": "new"}])
        assert updated is not None
        assert updated.messages[0]["content"] == "new"

    def test_update_nonexistent(self, manager):
        assert manager.update_session("nope", []) is None

    def test_workdir_isolation(self, store):
        mgr1 = SessionManager("/dir/one", store=store)
        mgr2 = SessionManager("/dir/two", store=store)
        mgr1.save_session([{"role": "user", "content": "from dir1"}])
        assert len(mgr2.list_sessions()) == 0

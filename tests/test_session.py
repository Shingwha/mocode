"""Session management tests"""

import json
from unittest.mock import patch

import pytest

from mocode.core.session import Session, SessionManager


@pytest.fixture
def session_dir(tmp_path):
    """Provide a temporary directory for sessions"""
    return tmp_path / "sessions"


@pytest.fixture
def manager(session_dir):
    """SessionManager using tmp_path"""
    with patch("mocode.core.session.SESSIONS_DIR", session_dir):
        mgr = SessionManager("/fake/workdir")
        mgr._sessions_dir = session_dir / mgr._workdir_hash
        return mgr


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
        assert d["messages"] == []

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
        assert restored.model == original.model


class TestSessionManager:
    def test_save_and_load(self, manager):
        messages = [{"role": "user", "content": "hello"}]
        session = manager.save_session(messages, model="gpt-4o", provider="openai")
        assert session.id.startswith("session_")
        assert session.message_count == 1

        loaded = manager.load_session(session.id)
        assert loaded is not None
        assert loaded.messages == messages

    def test_list_sessions(self, manager):
        from unittest.mock import patch
        from datetime import datetime

        # Each save_session calls datetime.now() twice: once for id, once for timestamp
        with patch("mocode.core.session.datetime") as mock_dt:
            mock_dt.now.side_effect = [
                datetime(2026, 1, 1, 10, 0, 0),  # save 1: id
                datetime(2026, 1, 1, 10, 0, 0),  # save 1: timestamp
                datetime(2026, 1, 1, 10, 0, 1),  # save 2: id
                datetime(2026, 1, 1, 10, 0, 1),  # save 2: timestamp
            ]
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            s1 = manager.save_session([{"role": "user", "content": "a"}])
            s2 = manager.save_session([{"role": "user", "content": "b"}])
        sessions = manager.list_sessions()
        assert len(sessions) == 2

    def test_list_sessions_sorted(self, manager):
        from unittest.mock import patch
        from datetime import datetime

        with patch("mocode.core.session.datetime") as mock_dt:
            mock_dt.now.side_effect = [
                datetime(2026, 1, 1, 10, 0, 0),  # save 1: id
                datetime(2026, 1, 1, 10, 0, 0),  # save 1: timestamp
                datetime(2026, 1, 1, 10, 0, 1),  # save 2: id
                datetime(2026, 1, 1, 10, 0, 1),  # save 2: timestamp
            ]
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            s1 = manager.save_session([{"role": "user", "content": "first"}])
            s2 = manager.save_session([{"role": "user", "content": "second"}])
        sessions = manager.list_sessions()
        # Most recent first
        assert sessions[0].id == s2.id
        assert sessions[1].id == s1.id

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

    def test_workdir_isolation(self, session_dir):
        with patch("mocode.core.session.SESSIONS_DIR", session_dir):
            mgr1 = SessionManager("/dir/one")
            mgr1._sessions_dir = session_dir / mgr1._workdir_hash
            mgr2 = SessionManager("/dir/two")
            mgr2._sessions_dir = session_dir / mgr2._workdir_hash

            mgr1.save_session([{"role": "user", "content": "from dir1"}])
            assert len(mgr2.list_sessions()) == 0

    def test_corrupt_session_skip(self, manager):
        manager._ensure_sessions_dir()
        corrupt_file = manager._sessions_dir / "session_corrupt.json"
        corrupt_file.write_text("{bad json", encoding="utf-8")
        sessions = manager.list_sessions()
        assert len(sessions) == 0

    def test_list_sessions_empty_dir(self, manager):
        assert manager.list_sessions() == []

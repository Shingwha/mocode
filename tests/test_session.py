"""Session management tests — v0.2"""

import pytest

from mocode.session import (
    Session,
    InMemorySessionStore,
    FileSessionStore,
    SessionManager,
)


@pytest.fixture
def store():
    return InMemorySessionStore()


@pytest.fixture
def manager(store):
    return SessionManager("/fake/workdir", store=store)


class TestSession:
    def test_from_dict(self):
        data = {
            "id": "session_abc123def456",
            "created_at": "2026-03-18T14:30:22",
            "updated_at": "2026-03-18T14:30:22",
            "workdir": "/home/user/project",
            "messages": [{"role": "user", "content": "hello"}],
            "model": "gpt-4o",
            "provider": "openai",
            "title": "hello",
            "metadata": {"source": "cli"},
        }
        s = Session.from_dict(data)
        assert s.id == "session_abc123def456"
        assert s.message_count == 1
        assert s.title == "hello"
        assert s.metadata == {"source": "cli"}

    def test_from_dict_defaults(self):
        data = {
            "id": "s1",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "workdir": "/tmp",
            "messages": [],
        }
        s = Session.from_dict(data)
        assert s.title == ""
        assert s.metadata == {}

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
        assert d["title"] == ""
        assert d["metadata"] == {}

    def test_roundtrip(self):
        original = Session(
            id="s_rt",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hi"}],
            model="m1",
            provider="p1",
            title="hi",
            metadata={"source": "web"},
        )
        restored = Session.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.messages == original.messages
        assert restored.title == original.title
        assert restored.metadata == original.metadata


class TestSessionManager:
    def test_create(self, manager):
        sid = manager.create()
        assert sid.startswith("session_")
        assert manager.active_id == sid
        assert not manager.is_dirty

    def test_create_with_metadata(self, manager):
        sid = manager.create(metadata={"source": "cli"})
        session = manager.resume(sid)
        assert session is not None
        assert session.metadata["source"] == "cli"

    def test_save_new(self, manager):
        messages = [{"role": "user", "content": "hello world"}]
        session = manager.save(messages, model="gpt-4o", provider="openai")
        assert session.id.startswith("session_")
        assert session.message_count == 1
        assert session.title == "hello world"
        assert manager.active_id == session.id
        assert not manager.is_dirty

    def test_save_truncates_title(self, manager):
        long_msg = "x" * 200
        session = manager.save([{"role": "user", "content": long_msg}])
        assert len(session.title) <= 80

    def test_save_upsert_existing(self, manager):
        session = manager.save([{"role": "user", "content": "old"}])
        original_id = session.id
        manager.mark_dirty()

        updated = manager.save([{"role": "user", "content": "new"}], model="m2")
        assert updated.id == original_id
        assert updated.messages[0]["content"] == "new"
        assert updated.model == "m2"

    def test_resume(self, manager):
        session = manager.save([{"role": "user", "content": "hello"}])
        loaded = manager.resume(session.id)
        assert loaded is not None
        assert loaded.messages == session.messages
        assert manager.active_id == session.id
        assert not manager.is_dirty

    def test_resume_nonexistent(self, manager):
        assert manager.resume("nope") is None

    def test_list_empty(self, manager):
        assert manager.list() == []

    def test_list_with_source_filter(self, manager):
        manager.create(metadata={"source": "cli"})
        manager.create(metadata={"source": "web"})
        manager._active_id = None

        cli_sessions = manager.list(source="cli")
        assert len(cli_sessions) == 1
        assert cli_sessions[0].metadata["source"] == "cli"

        web_sessions = manager.list(source="web")
        assert len(web_sessions) == 1

    def test_list_with_channel_filter(self, manager):
        manager.create(metadata={"source": "gateway", "channel": "weixin"})
        manager.create(metadata={"source": "gateway", "channel": "feishu"})
        manager._active_id = None

        weixin = manager.list(channel="weixin")
        assert len(weixin) == 1
        assert weixin[0].metadata["channel"] == "weixin"

    def test_delete(self, manager):
        session = manager.save([{"role": "user", "content": "x"}])
        assert manager.delete(session.id)
        assert manager.resume(session.id) is None
        assert manager.active_id is None

    def test_delete_nonexistent(self, manager):
        assert not manager.delete("nope")

    def test_clear(self, manager):
        manager.create()
        assert manager.active_id is not None
        manager.mark_dirty()
        manager.clear()
        assert manager.active_id is None
        assert not manager.is_dirty

    def test_invalidate(self, manager):
        manager.create()
        manager.invalidate()
        assert manager.active_id is None
        assert manager.is_dirty

    def test_mark_dirty(self, manager):
        assert not manager.is_dirty
        manager.mark_dirty()
        assert manager.is_dirty

    def test_save_if_dirty_skips_when_clean(self, manager):
        result = manager.save_if_dirty([], "m", "p")
        assert result is None

    def test_save_if_dirty_saves_when_dirty(self, manager):
        manager.mark_dirty()
        result = manager.save_if_dirty(
            [{"role": "user", "content": "hi"}], "m", "p"
        )
        assert result is not None
        assert not manager.is_dirty

    def test_workdir_property(self, manager):
        assert manager.workdir == "/fake/workdir"

    def test_workdir_isolation(self, store):
        mgr1 = SessionManager("/dir/one", store=store)
        mgr2 = SessionManager("/dir/two", store=store)
        mgr1.save([{"role": "user", "content": "from dir1"}])
        assert len(mgr2.list()) == 0


class TestInMemorySessionStore:
    def test_save_and_load(self, store):
        session = Session(
            id="s1",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            workdir="/tmp",
            messages=[{"role": "user", "content": "hello"}],
        )
        store.save("/tmp", session)
        loaded = store.load("/tmp", "s1")
        assert loaded is not None
        assert loaded.id == "s1"

    def test_list_sorted_by_updated_at(self, store):
        s1 = Session(id="s1", created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00", workdir="/tmp", messages=[])
        s2 = Session(id="s2", created_at="2026-01-02T00:00:00", updated_at="2026-01-02T00:00:00", workdir="/tmp", messages=[])
        store.save("/tmp", s1)
        store.save("/tmp", s2)
        sessions = store.list("/tmp")
        assert sessions[0].id == "s2"

    def test_delete(self, store):
        session = Session(id="s1", created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00", workdir="/tmp", messages=[])
        store.save("/tmp", session)
        assert store.delete("/tmp", "s1")
        assert store.load("/tmp", "s1") is None

    def test_delete_nonexistent(self, store):
        assert not store.delete("/tmp", "nope")

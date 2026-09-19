"""Session records and the store that holds them."""

import pytest

from mocode.host.export import export_session, export_session_md
from mocode.host.session import (
    Session,
    SessionStore,
    load_session_file,
    new_session_id,
)


def _session(
    session_id: str = "session_abc",
    *,
    workdir: str = "/project",
    messages: list[dict] | None = None,
    updated_at: str = "2025-01-01T00:00:00",
    **kwargs,
) -> Session:
    return Session(
        id=session_id,
        created_at="2025-01-01T00:00:00",
        updated_at=updated_at,
        workdir=workdir,
        messages=messages if messages is not None else [],
        **kwargs,
    )


class TestSession:
    def test_create(self):
        s = _session(messages=[{"role": "user", "content": "hi"}])
        assert s.id == "session_abc"
        assert len(s.messages) == 1
        assert s.title == ""
        assert s.metadata == {}

    def test_to_dict_roundtrip(self):
        s = _session(
            messages=[{"role": "user", "content": "hi"}],
            title="test",
            model="gpt-4o",
            provider="openai",
            metadata={"key": "value"},
        )
        s2 = Session.from_dict(s.to_dict())
        assert s2.id == s.id
        assert s2.title == s.title
        assert s2.messages == s.messages
        assert s2.metadata == {"key": "value"}

    def test_new_id_is_unique_and_prefixed(self):
        first, second = new_session_id(), new_session_id()
        assert first.startswith("session_") and first != second


class TestSessionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return SessionStore(base_dir=tmp_path / "sessions")

    def test_save_and_load(self, store):
        store.save("/project", _session())
        loaded = store.load("/project", "session_abc")
        assert loaded is not None
        assert loaded.id == "session_abc"

    def test_list_sorted_most_recent_first(self, store):
        store.save("/p", _session("session_s1", updated_at="2025-01-01T00:00:00"))
        store.save("/p", _session("session_s2", updated_at="2025-01-02T00:00:00"))
        sessions = store.list("/p")
        assert [s.id for s in sessions] == ["session_s2", "session_s1"]

    def test_projects_do_not_see_each_other(self, store):
        store.save("/p", _session("session_a", workdir="/p"))
        store.save("/other", _session("session_b", workdir="/other"))
        assert [s.id for s in store.list("/p")] == ["session_a"]

    def test_delete(self, store):
        store.save("/p", _session("session_s1"))
        assert store.delete("/p", "session_s1") is True
        assert store.load("/p", "session_s1") is None

    def test_listing_nothing_is_empty(self, store):
        assert store.list("/nope") == []
        assert store.list_all() == []

    def test_list_all_spans_projects(self, store):
        """A UI groups by project, so the record has to carry its own."""
        store.save("/p", _session("session_a", workdir="/p", updated_at="2025-01-01T00:00:00"))
        store.save("/other", _session("session_b", workdir="/other", updated_at="2025-01-02T00:00:00"))

        everything = store.list_all()

        assert [(s.id, s.workdir) for s in everything] == [
            ("session_b", "/other"),
            ("session_a", "/p"),
        ]

    def test_find_by_id_alone(self, store):
        """A link carries an id, not a project — the store has to do the looking."""
        store.save("/somewhere/deep", _session("session_x", workdir="/somewhere/deep"))
        assert store.find("session_x").workdir == "/somewhere/deep"
        assert store.find("session_missing") is None


class TestPortability:
    def test_export_and_import(self, tmp_path):
        session = _session(messages=[{"role": "user", "content": "hello"}])
        path = tmp_path / "export.json"

        export_session(path, session, system_prompt="You are helpful.")

        result = load_session_file(path)
        assert result is not None
        messages, _title = result
        assert messages == session.messages

    def test_import_nonexistent(self, tmp_path):
        assert load_session_file(tmp_path / "nope.json") is None
        assert load_session_file(tmp_path / "wrong.txt") is None

    def test_export_to_md_basic(self, tmp_path):
        session = _session(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
            model="gpt-4o",
            provider="openai",
        )
        path = tmp_path / "export.md"

        export_session_md(session, path, system_prompt="You are helpful.")

        md = path.read_text(encoding="utf-8")
        assert md.startswith("---")
        assert "session_id:" in md
        assert "model: gpt-4o" in md
        assert "## System Prompt" in md
        assert "You are helpful." in md
        assert "## Turn 1" in md
        assert "### User" in md
        assert "hello" in md
        assert "### Assistant" in md
        assert "hi there" in md

    def test_export_to_md_with_tool_calls(self, tmp_path):
        session = _session(
            messages=[
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
        )
        path = tmp_path / "export.md"

        export_session_md(session, path, system_prompt="")

        md = path.read_text(encoding="utf-8")
        assert "#### Tool Call: read" in md
        assert "call_1" in md
        assert "Tool Result: read" in md
        assert "print('hello')" in md
        assert "Here is the file content." in md

    def test_export_to_md_with_reasoning(self, tmp_path):
        session = _session(
            messages=[
                {"role": "user", "content": "think about this"},
                {
                    "role": "assistant",
                    "content": "The answer is 42.",
                    "reasoning_content": "Let me think step by step...",
                },
            ]
        )
        path = tmp_path / "export.md"

        export_session_md(session, path)

        md = path.read_text(encoding="utf-8")
        assert "<details><summary>Thinking</summary>" in md
        assert "Let me think step by step..." in md
        assert "The answer is 42." in md

    def test_export_to_md_empty_session(self, tmp_path):
        path = tmp_path / "export.md"

        export_session_md(_session(), path, system_prompt="Be brief.")

        md = path.read_text(encoding="utf-8")
        assert md.startswith("---")
        assert "## System Prompt" in md
        assert "## Turn" not in md

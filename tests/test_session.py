"""Session records and the store that holds them."""

import json
import re

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


def _frontmatter(md: str) -> dict[str, str]:
    """导出文件头部的 YAML 风格键值——记录自身的身份数据。"""
    lines = md.splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    return dict(line.split(": ", 1) for line in lines[1:end] if ": " in line)


def _sections(md: str) -> list[tuple[int, str, str]]:
    """导出文档的骨架：``[(标题层级, 标题, 正文)]``。

    Markdown 导出的契约是结构——section 顺序、每条消息一节、每个 tool call 一
    个区块——所以按标题层级解析，不断言任何标题文案。
    """
    blocks: list[tuple[int, str, list[str]]] = []

    for line in md.splitlines():
        match = re.fullmatch(r"(#{2,4}) (.+)", line)
        if match:
            blocks.append((len(match.group(1)), match.group(2), []))
        elif blocks and line.strip() != "---":
            blocks[-1][2].append(line)
    return [(level, title, "\n".join(body).strip()) for level, title, body in blocks]


def _fenced(text: str) -> str:
    """```json 围栏里的内容。"""
    match = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert match is not None
    return match.group(1)


class TestSession:
    def test_a_session_round_trips_and_defaults_are_empty(self):
        """默认干净：没有标题、没有元数据；声明过的字段逐个原样往返。"""
        fresh = _session(messages=[{"role": "user", "content": "hi"}])
        assert fresh.id == "session_abc"
        assert len(fresh.messages) == 1
        assert fresh.title == ""
        assert fresh.metadata == {}

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

    def test_save_find_and_delete(self, store):
        store.save("/project", _session())
        loaded = store.find("session_abc")
        assert loaded is not None
        assert loaded.id == "session_abc"

        assert store.delete("/p", "session_nope") is False
        assert store.delete("/project", "session_abc") is True
        assert store.find("session_abc") is None

    def test_listing_nothing_is_empty_and_projects_do_not_see_each_other(self, store):
        assert store.list("/nope") == []
        assert store.list_all() == []

        store.save("/p", _session("session_a", workdir="/p"))
        store.save("/other", _session("session_b", workdir="/other"))
        assert [s.id for s in store.list("/p")] == ["session_a"]

    def test_listing_is_most_recent_first_and_spans_projects(self, store):
        """A UI groups by project, so the record has to carry its own —
        列表按最近更新排，全量列表跨项目也带得出 workdir。"""
        store.save("/p", _session("session_s1", workdir="/p", updated_at="2025-01-01T00:00:00"))
        store.save("/p", _session("session_s2", workdir="/p", updated_at="2025-01-02T00:00:00"))
        assert [s.id for s in store.list("/p")] == ["session_s2", "session_s1"]

        store.save(
            "/other",
            _session("session_b", workdir="/other", updated_at="2025-01-03T00:00:00"),
        )
        assert [(s.id, s.workdir) for s in store.list_all()] == [
            ("session_b", "/other"),
            ("session_s2", "/p"),
            ("session_s1", "/p"),
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

        # 不存在的文件、或根本不是导出格式的文件：读不出来，而不是崩
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
        front = _frontmatter(md)
        assert front["model"] == "gpt-4o"
        assert front["message_count"] == "2"

        blocks = _sections(md)
        # 每条消息一个小节（L3），顺序即会话顺序
        messages = [b for b in blocks if b[0] == 3]
        assert [body for _, _, body in messages] == ["hello", "hi there"]
        # system prompt 一节在最前：先于任何消息小节
        prompt = next(b for b in blocks if b[2] == "You are helpful.")
        assert blocks.index(prompt) < blocks.index(messages[0])
        # 一个 turn 小节把消息括起来（连同概览与 system prompt 共三节 L2）
        assert len([b for b in blocks if b[0] == 2]) == 3

        # 空会话：没有任何消息层级的小节；system prompt 一节仍是最后一节
        empty_path = tmp_path / "empty.md"
        export_session_md(_session(), empty_path, system_prompt="Be brief.")

        empty_blocks = _sections(empty_path.read_text(encoding="utf-8"))
        assert _frontmatter(empty_path.read_text(encoding="utf-8"))["message_count"] == "0"
        assert [b for b in empty_blocks if b[0] == 3] == []
        assert empty_blocks[-1][2] == "Be brief."

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
        blocks = _sections(md)
        # tool call 区块数与会话里的 call 数一致（这一个）
        calls = [b for b in blocks if b[0] == 4]
        assert len(calls) == 1
        # 参数以可解析的 JSON 落在区块里，call id 是关联记号
        assert json.loads(_fenced(calls[0][2])) == {"path": "test.py"}
        assert "call_1" in calls[0][2]
        # 结果跟在 call 之后，收尾的 assistant 消息在最后
        assert md.index("call_1") < md.index("print('hello')")
        assert md.index("print('hello')") < md.index("Here is the file content.")

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
        messages = [b for b in _sections(md) if b[0] == 3]
        assert len(messages) == 2
        assistant = messages[1][2]
        # 推理内容收在折叠块里、出现在答案之前
        assert assistant.index("<details>") < assistant.index(
            "Let me think step by step..."
        )
        assert assistant.index("Let me think step by step...") < assistant.index(
            "The answer is 42."
        )


class TestPluginMessagesField:
    def test_round_trip(self):
        messages = [
            {
                "type": "plugin_message",
                "run_id": "run_1",
                "seq": 3,
                "kind": "rag/index",
                "data": {"done": 12, "total": 40},
                "block_id": "rag-1",
                "sealed": False,
            },
            {
                "type": "plugin_message",
                "run_id": "run_1",
                "seq": 4,
                "kind": "",
                "data": {},
                "block_id": "rag-1",
                "sealed": True,
            },
        ]
        session = _session(plugin_messages=messages)

        assert Session.from_dict(session.to_dict()) == session
        assert Session.from_dict(session.to_dict()).plugin_messages == messages

    def test_malformed_values_leave_a_usable_field(self):
        """坏条目整条丢掉，整个字段形状不对则一篇没有——字段本身仍可用。"""
        cases = [
            ([{"kind": "kept"}, "junk", 42, None, ["x"]], [{"kind": "kept"}]),
            ({"kind": "not-a-list"}, []),
        ]
        for raw, expected in cases:
            session = Session.from_dict(
                {
                    "id": "session_x",
                    "created_at": "t",
                    "updated_at": "t",
                    "workdir": "/project",
                    "messages": [],
                    "plugin_messages": raw,
                }
            )
            assert session.plugin_messages == expected


class TestFrozenRequestFields:
    """会话冻结的请求面（prompt、工具接口）与插件自有状态。

    三者都是可迁入迁出的可选字段：新会话从插件现场拿，旧文件没有它们也能读。
    """

    def test_round_trip(self):
        session = _session(
            system_prompt="<system-prompt>frozen</system-prompt>",
            tool_schemas=[{"function": {"name": "read"}}],
            plugin_state={
                "cache-protect": {"prompt": "<system-prompt>frozen</system-prompt>"}
            },
        )

        assert Session.from_dict(session.to_dict()) == session

    def test_absent_for_legacy_files(self):
        """旧文件一个可迁字段都没有：每个字段拿自己的缺省值。"""
        legacy = Session.from_dict(
            {
                "id": "session_old",
                "created_at": "t",
                "updated_at": "t",
                "workdir": "/project",
                "messages": [],
            }
        )
        for field, default in [
            ("plugin_messages", []),
            ("system_prompt", ""),
            ("tool_schemas", []),
            ("plugin_state", {}),
        ]:
            assert getattr(legacy, field) == default, field

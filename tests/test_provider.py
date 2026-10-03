"""The OpenAI-compatible provider: request shaping and chunk mapping.

A fake client stands in for the SDK, so these run without openai installed and
without network access.
"""

from __future__ import annotations

from types import SimpleNamespace

from mocode.core.provider import StreamAccumulator
from mocode.providers.openai import OpenAIProvider


class _FakeStream:
    """Whatever the fake client returns — async-iterable, like the real stream."""

    def __init__(self, chunks: list):
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


class _FakeCompletions:
    def __init__(self, sink: list[dict], chunks: list):
        self._sink = sink
        self._chunks = chunks

    async def create(self, **kwargs):
        self._sink.append(kwargs)
        return _FakeStream(self._chunks)


def _provider(sink: list[dict], chunks: list | None = None, **kwargs) -> OpenAIProvider:
    provider = OpenAIProvider(api_key="sk-test", model="test-model", **kwargs)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(sink, chunks or []))
    )
    return provider


def _delta(text=None, reasoning=None, tool_calls=None, finish=None, usage=None):
    delta = SimpleNamespace(
        content=text, reasoning_content=reasoning, tool_calls=tool_calls
    )
    choices = [] if (usage is not None and text is None and finish is None) else [
        SimpleNamespace(delta=delta, finish_reason=finish)
    ]
    return SimpleNamespace(choices=choices, usage=usage)


def _tool_part(index: int, id: str = "", name: str = "", arguments: str = ""):
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


async def _stream(provider, messages=None, system="sys", tools=None, max_tokens=None, effort=None):
    return [
        chunk
        async for chunk in provider.stream(messages or [], system, tools or [], max_tokens, effort)
    ]


class TestRequestShape:
    """wire payload 的字段级契约：一个字段要么在场、要么缺席。

    四种形态合进一张字段表：``expected=None`` 表示该字段必须根本不出现在
    请求里；红了看断言消息即知是哪个字段的哪种形态。"""

    async def test_one_field_of_the_request(self):
        cases = [
            ({}, "max_tokens", None),
            ({"max_tokens": 4096}, "max_tokens", 4096),
            ({}, "reasoning_effort", None),
            ({"effort": "high"}, "reasoning_effort", "high"),
        ]
        for stream_kwargs, field, expected in cases:
            sent: list[dict] = []
            await _stream(_provider(sent), **stream_kwargs)
            if expected is None:
                assert field not in sent[0], f"{field} absent for {stream_kwargs}"
            else:
                assert sent[0][field] == expected, f"{field} for {stream_kwargs}"

    async def test_system_prompt_is_prepended(self):
        sent: list[dict] = []
        await _stream(_provider(sent), messages=[{"role": "user", "content": "hi"}], system="SYS")
        assert sent[0]["messages"][0] == {"role": "system", "content": "SYS"}

    async def test_empty_tools_become_none_and_streaming_is_requested(self):
        sent: list[dict] = []
        await _stream(_provider(sent))
        assert sent[0]["tools"] is None
        assert sent[0]["stream"] is True
        assert sent[0]["stream_options"] == {"include_usage": True}
        assert sent[0]["model"] == "test-model"


class TestChunkMapping:
    async def test_text_and_reasoning(self):
        sent: list[dict] = []
        provider = _provider(sent, [_delta("a"), _delta(reasoning="why"), _delta(finish="stop")])

        chunks = await _stream(provider)

        assert "".join(c.text for c in chunks) == "a"
        assert "".join(c.reasoning for c in chunks) == "why"
        assert chunks[-1].finish_reason == "stop"

        # 什么都不带的 delta 整条丢掉，不在输出里占位
        dropped: list[dict] = []
        provider = _provider(dropped, [_delta(), _delta("real")])
        assert [c.text for c in await _stream(provider)] == ["real"]

    async def test_usage_only_chunk_is_kept(self):
        sent: list[dict] = []
        usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4)
        provider = _provider(sent, [_delta("hi"), _delta(usage=usage)])

        chunks = await _stream(provider)

        assert chunks[-1].usage.prompt_tokens == 3
        assert chunks[-1].usage.completion_tokens == 4

    async def test_tool_call_fragments_are_mapped(self):
        sent: list[dict] = []
        provider = _provider(
            sent,
            [
                _delta(tool_calls=[_tool_part(0, id="c1", name="echo")]),
                _delta(tool_calls=[_tool_part(0, arguments='{"v"')]),
            ],
        )

        chunks = await _stream(provider)

        assert chunks[0].tool_calls[0].name == "echo"
        assert chunks[1].tool_calls[0].arguments == '{"v"'


class TestStreamAccumulator:
    def _feed(self, *chunks) -> StreamAccumulator:
        acc = StreamAccumulator()
        for chunk in chunks:
            acc.feed(chunk)
        return acc

    def test_text_and_usage_accumulate(self):
        from mocode.core.provider import Chunk, Usage

        acc = self._feed(
            Chunk(text="Hel"), Chunk(text="lo"), Chunk(usage=Usage(2, 3), finish_reason="stop")
        )
        response = acc.build()
        assert response.content == "Hello"
        assert response.usage == Usage(2, 3)
        assert response.finish_reason == "stop"

    def test_tool_call_fragments_join_by_index(self):
        from mocode.core.provider import Chunk, ToolCallDelta

        acc = self._feed(
            Chunk(tool_calls=[ToolCallDelta(index=0, id="c1", name="echo")]),
            Chunk(tool_calls=[ToolCallDelta(index=0, arguments='{"a":')]),
            Chunk(tool_calls=[ToolCallDelta(index=0, arguments="1}")]),
        )
        calls = acc.build().tool_calls
        assert [(c.id, c.name, c.arguments) for c in calls] == [("c1", "echo", '{"a":1}')]

        # 交错到达的另一路调用：下标才是归属，到达顺序不是
        interleaved = self._feed(
            Chunk(tool_calls=[ToolCallDelta(index=0, id="c1", name="a")]),
            Chunk(tool_calls=[ToolCallDelta(index=1, id="c2", name="b")]),
            Chunk(tool_calls=[ToolCallDelta(index=1, arguments="{}")]),
            Chunk(tool_calls=[ToolCallDelta(index=0, arguments="{}")]),
        )
        assert [c.name for c in interleaved.build().tool_calls] == ["a", "b"]

    def test_parallel_calls_in_one_chunk(self):
        """One delta may carry several call fragments; none may be lost."""
        from mocode.core.provider import Chunk, ToolCallDelta

        acc = self._feed(
            Chunk(
                tool_calls=[
                    ToolCallDelta(index=0, id="c1", name="a"),
                    ToolCallDelta(index=1, id="c2", name="b", arguments="{}"),
                ]
            ),
            Chunk(tool_calls=[ToolCallDelta(index=0, arguments="{}")]),
        )
        assert [(c.name, c.arguments) for c in acc.build().tool_calls] == [
            ("a", "{}"),
            ("b", "{}"),
        ]

    def test_an_empty_stream_builds_a_contentless_response(self):
        response = StreamAccumulator().build()
        assert response.content is None
        assert response.tool_calls is None


class TestUnansweredToolCalls:
    """An endpoint refuses a tool call whose answer is gone from the history,
    so the outgoing payload is cleaned of them — assert on what is sent."""

    async def test_an_orphaned_tool_call_loses_its_field(self):
        """The answer vanished, so the call goes with it — a field, not a
        message；答案一条不缺的历史原样出门。"""
        sent: list[dict] = []
        await _stream(_provider(sent), messages=[{"role": "user", "content": "hi"}])
        assert sent[0]["messages"][1:] == [{"role": "user", "content": "hi"}]

        sent: list[dict] = []
        await _stream(
            _provider(sent),
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "c1", "type": "function", "function": {}}],
                },
                {"role": "user", "content": "next"},
            ],
        )
        assert sent[0]["messages"][1] == {"role": "assistant", "content": ""}
        assert sent[0]["messages"][2] == {"role": "user", "content": "next"}

    async def test_only_the_unanswered_call_is_dropped(self):
        """One answered call keeps its partner sent; only the orphan goes."""
        sent: list[dict] = []
        await _stream(
            _provider(sent),
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {}},
                        {"id": "c2", "type": "function", "function": {}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "result"},
            ],
        )
        assert [tc["id"] for tc in sent[0]["messages"][1]["tool_calls"]] == ["c1"]
        assert sent[0]["messages"][2] == {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "result",
        }

"""The OpenAI-compatible provider: request shaping and chunk mapping.

A fake client stands in for the SDK, so these run without openai installed and
without network access.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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


async def _stream(provider, messages=None, system="sys", tools=None, max_tokens=None):
    return [
        chunk
        async for chunk in provider.stream(messages or [], system, tools or [], max_tokens)
    ]


class TestRequestShape:
    @pytest.mark.asyncio
    async def test_no_cap_means_no_max_tokens_field(self):
        """An unset cap must not be turned into a made-up number."""
        sent: list[dict] = []
        await _stream(_provider(sent), max_tokens=None)
        assert "max_tokens" not in sent[0]

    @pytest.mark.asyncio
    async def test_explicit_cap_is_sent(self):
        sent: list[dict] = []
        await _stream(_provider(sent), max_tokens=4096)
        assert sent[0]["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_system_prompt_is_prepended(self):
        sent: list[dict] = []
        await _stream(_provider(sent), messages=[{"role": "user", "content": "hi"}], system="SYS")
        assert sent[0]["messages"][0] == {"role": "system", "content": "SYS"}

    @pytest.mark.asyncio
    async def test_empty_tools_become_none_and_streaming_is_requested(self):
        sent: list[dict] = []
        await _stream(_provider(sent))
        assert sent[0]["tools"] is None
        assert sent[0]["stream"] is True
        assert sent[0]["stream_options"] == {"include_usage": True}
        assert sent[0]["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_extra_body_is_passed_through(self):
        sent: list[dict] = []
        provider = _provider(sent, extra_body={"thinking": {"type": "enabled"}})
        await _stream(provider)
        assert sent[0]["extra_body"] == {"thinking": {"type": "enabled"}}

    @pytest.mark.asyncio
    async def test_stream_options_may_be_overridden_from_extra_body(self):
        """An endpoint that rejects the parameter can be configured around it."""
        sent: list[dict] = []
        provider = _provider(
            sent, extra_body={"stream_options": {"include_usage": False}, "top_k": 5}
        )
        await _stream(provider)
        assert sent[0]["stream_options"] == {"include_usage": False}
        # Lifted out of extra_body, not sent twice.
        assert sent[0]["extra_body"] == {"top_k": 5}


class TestChunkMapping:
    @pytest.mark.asyncio
    async def test_text_and_reasoning(self):
        sent: list[dict] = []
        provider = _provider(sent, [_delta("a"), _delta(reasoning="why"), _delta(finish="stop")])

        chunks = await _stream(provider)

        assert "".join(c.text for c in chunks) == "a"
        assert "".join(c.reasoning for c in chunks) == "why"
        assert chunks[-1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_empty_deltas_are_dropped(self):
        sent: list[dict] = []
        provider = _provider(sent, [_delta(), _delta("real")])
        assert [c.text for c in await _stream(provider)] == ["real"]

    @pytest.mark.asyncio
    async def test_usage_only_chunk_is_kept(self):
        sent: list[dict] = []
        usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4)
        provider = _provider(sent, [_delta("hi"), _delta(usage=usage)])

        chunks = await _stream(provider)

        assert chunks[-1].usage.prompt_tokens == 3
        assert chunks[-1].usage.completion_tokens == 4

    @pytest.mark.asyncio
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

    def test_interleaved_tool_calls_stay_separate(self):
        from mocode.core.provider import Chunk, ToolCallDelta

        acc = self._feed(
            Chunk(tool_calls=[ToolCallDelta(index=0, id="c1", name="a")]),
            Chunk(tool_calls=[ToolCallDelta(index=1, id="c2", name="b")]),
            Chunk(tool_calls=[ToolCallDelta(index=1, arguments="{}")]),
            Chunk(tool_calls=[ToolCallDelta(index=0, arguments="{}")]),
        )
        assert [c.name for c in acc.build().tool_calls] == ["a", "b"]

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


class TestNormalizeMessages:
    def test_unchanged_when_no_tool_calls(self):
        messages = [{"role": "user", "content": "hi"}]
        assert OpenAIProvider._normalize_messages(messages) == messages

    def test_strips_orphaned_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {}}],
            },
            {"role": "user", "content": "next"},
        ]
        assert "tool_calls" not in OpenAIProvider._normalize_messages(messages)[0]

    def test_drops_only_unmatched_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {}},
                    {"id": "c2", "type": "function", "function": {}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        normalized = OpenAIProvider._normalize_messages(messages)
        assert [tc["id"] for tc in normalized[0]["tool_calls"]] == ["c1"]

"""Tests for the OpenAI-compatible provider's request shaping.

A fake client stands in for the SDK, so these run without openai installed
and without network access.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mocode.providers.openai import OpenAIProvider


class _FakeCompletions:
    def __init__(self, sink: list[dict]):
        self._sink = sink

    async def create(self, **kwargs):
        self._sink.append(kwargs)
        message = SimpleNamespace(content="ok", tool_calls=None, reasoning_content=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=None)


def _provider(sink: list[dict], **kwargs) -> OpenAIProvider:
    provider = OpenAIProvider(api_key="sk-test", model="test-model", **kwargs)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(sink))
    )
    return provider


class TestRequestShape:
    @pytest.mark.asyncio
    async def test_no_cap_means_no_max_tokens_field(self):
        """An unset cap must not be turned into a made-up number."""
        sent: list[dict] = []
        await _provider(sent).call([], "sys", [], None)
        assert "max_tokens" not in sent[0]

    @pytest.mark.asyncio
    async def test_explicit_cap_is_sent(self):
        sent: list[dict] = []
        await _provider(sent).call([], "sys", [], 4096)
        assert sent[0]["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_system_prompt_is_prepended(self):
        sent: list[dict] = []
        await _provider(sent).call([{"role": "user", "content": "hi"}], "SYSTEM", [], None)
        assert sent[0]["messages"][0] == {"role": "system", "content": "SYSTEM"}

    @pytest.mark.asyncio
    async def test_empty_tools_become_none(self):
        sent: list[dict] = []
        await _provider(sent).call([], "sys", [], None)
        assert sent[0]["tools"] is None

    @pytest.mark.asyncio
    async def test_extra_body_is_passed_through(self):
        sent: list[dict] = []
        provider = _provider(sent, extra_body={"thinking": {"type": "enabled"}})
        await provider.call([], "sys", [], None)
        assert sent[0]["extra_body"] == {"thinking": {"type": "enabled"}}

    @pytest.mark.asyncio
    async def test_model_name_is_sent(self):
        sent: list[dict] = []
        await _provider(sent).call([], "sys", [], None)
        assert sent[0]["model"] == "test-model"


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
        normalized = OpenAIProvider._normalize_messages(messages)
        assert "tool_calls" not in normalized[0]

    def test_keeps_matched_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        normalized = OpenAIProvider._normalize_messages(messages)
        assert normalized[0]["tool_calls"] == messages[0]["tool_calls"]

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

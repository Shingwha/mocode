"""Tests for compact — compact_messages, CompactHook, CompactTool."""

import pytest

from mocode.core import AgentHookContext, Response, Usage
from mocode.tools.compact import (
    compact_messages,
    CompactTool,
)
from mocode.hooks.compact import CompactHook


class MockProvider:
    def __init__(self, responses=None, model="test-model"):
        self._responses = responses or []
        self._call_count = 0
        self._model = model

    @property
    def model(self):
        return self._model

    async def call(self, messages, system, tools, max_tokens):
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return Response(content="default response")


class MockAgent:
    """Minimal agent-like object with a .provider attribute."""

    def __init__(self, provider):
        self.provider = provider


# ---- compact_messages (pure function) ----


class TestCompactMessages:
    @pytest.mark.asyncio
    async def test_generates_summary(self):
        provider = MockProvider(responses=[Response(content="Summary of conversation")])

        messages = [
            {"role": "user", "content": "Fix the auth bug"},
            {"role": "assistant", "content": "I fixed it in auth.py"},
            {"role": "user", "content": "Now add tests"},
            {"role": "assistant", "content": "Added test_auth.py"},
        ]
        result = await compact_messages(provider, messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert "Summary of conversation" in result[0]["content"]


# ---- CompactHook ----


class TestCompactHook:
    @pytest.mark.asyncio
    async def test_before_iteration_compacts_when_over_threshold(self):
        provider = MockProvider(responses=[Response(content="summary")])
        hook = CompactHook(MockAgent(provider), threshold=0.80, context_window=128_000)
        hook._last_prompt_tokens = 110_000

        messages = [
            {"role": "user", "content": "old 1"},
            {"role": "assistant", "content": "resp 1"},
            {"role": "user", "content": "old 2"},
            {"role": "assistant", "content": "resp 2"},
            {"role": "user", "content": "old 3"},
            {"role": "assistant", "content": "resp 3"},
        ]
        original_len = len(messages)
        ctx = AgentHookContext(messages=messages)
        await hook.before_iteration(ctx)
        assert len(ctx.messages) < original_len

    @pytest.mark.asyncio
    async def test_before_iteration_skips_when_under_threshold(self):
        hook = CompactHook(
            MockAgent(MockProvider()), threshold=0.80, context_window=128_000
        )
        hook._last_prompt_tokens = 10_000

        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "resp"},
        ]
        ctx = AgentHookContext(messages=messages)
        await hook.before_iteration(ctx)
        assert ctx.messages is messages


# ---- CompactTool ----


class TestCompactTool:
    @pytest.mark.asyncio
    async def test_compacts_and_mutates_in_place(self):
        provider = MockProvider(responses=[Response(content="compressed summary")])
        messages = [
            {"role": "user", "content": "fix bug"},
            {"role": "assistant", "content": "fixed"},
            {"role": "user", "content": "add tests"},
            {"role": "assistant", "content": "added"},
        ]
        tool = CompactTool(MockAgent(provider), lambda: messages)
        result = await tool.run_async({})

        assert "compacted" in result.lower()
        assert len(messages) == 1
        assert "[Context Summary]" in messages[0]["content"]

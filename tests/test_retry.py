"""Retry semantics — the window closes at the first chunk."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from mocode.core.provider import Chunk, _compute_delay, with_retry_stream

_rate = type("RateLimitError", (Exception,), {})
_auth = type("AuthenticationError", (Exception,), {})


class _MockProvider:
    """Mimics OpenAI's error classification."""

    @property
    def model(self) -> str:
        return "mock"

    def is_retriable(self, exc: Exception) -> bool:
        return isinstance(exc, _rate)


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


def _streams(attempts: list):
    """A stream factory that replays *attempts*, one per call."""
    remaining = list(attempts)

    async def factory(*args, **kwargs):
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        for text in outcome:
            yield Chunk(text=text)

    return factory


async def _collect(provider, factory, *args, **kwargs) -> list[str]:
    return [c.text async for c in with_retry_stream(provider, factory, *args, **kwargs)]


class TestWithRetryStream:
    @pytest.mark.asyncio
    async def test_chunks_pass_through(self):
        factory = _streams([["a", "b"]])
        assert await _collect(_MockProvider(), factory) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_retries_before_the_first_chunk(self):
        factory = _streams([_rate("429"), _rate("429"), ["ok"]])
        assert await _collect(_MockProvider(), factory, max_retries=3) == ["ok"]

    @pytest.mark.asyncio
    async def test_error_after_the_first_chunk_is_not_replayed(self):
        """A stream cannot be replayed — a caller has already seen the chunks."""

        async def factory(*args, **kwargs):
            yield Chunk(text="partial")
            raise _rate("429")

        with pytest.raises(_rate):
            await _collect(_MockProvider(), factory, max_retries=3)

    @pytest.mark.asyncio
    async def test_non_retriable_error_propagates_immediately(self):
        factory = _streams([_auth("bad key")])
        with pytest.raises(_auth):
            await _collect(_MockProvider(), factory, max_retries=3)

    @pytest.mark.asyncio
    async def test_retries_are_exhausted(self):
        factory = _streams([_rate("429")] * 3)
        with pytest.raises(_rate):
            await _collect(_MockProvider(), factory, max_retries=2)

    @pytest.mark.asyncio
    async def test_cancellation_is_never_retried(self):
        factory = _streams([asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await _collect(_MockProvider(), factory, max_retries=3)

    @pytest.mark.asyncio
    async def test_arguments_are_forwarded(self):
        seen = []

        async def factory(*args, **kwargs):
            seen.append((args, kwargs))
            yield Chunk(text="ok")

        await _collect(_MockProvider(), factory, "a", "b", key="val")
        assert seen == [(("a", "b"), {"key": "val"})]

    @pytest.mark.asyncio
    async def test_an_empty_stream_is_not_an_error(self):
        factory = _streams([[]])
        assert await _collect(_MockProvider(), factory) == []


class TestComputeDelay:
    def test_grows_then_caps(self):
        assert _compute_delay(0) < _compute_delay(1) < _compute_delay(2)
        assert 1.0 <= _compute_delay(0) <= 1.5  # base plus at most the jitter
        assert _compute_delay(20) <= 60.0

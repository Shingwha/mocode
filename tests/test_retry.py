"""Retry semantics — the window closes at the first chunk."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from mocode.core.provider import Chunk, _compute_delay, with_retry_stream

_rate = type("RateLimitError", (Exception,), {})
_auth = type("AuthenticationError", (Exception,), {})


class _MockProvider:
    """Mimics OpenAI's error classification and replays one stream per call."""

    def __init__(self, attempts: list):
        self._remaining = list(attempts)

    @property
    def model(self) -> str:
        return "mock"

    def is_retriable(self, exc: Exception) -> bool:
        return isinstance(exc, _rate)

    async def stream(self, *args, **kwargs):
        outcome = self._remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        for text in outcome:
            yield Chunk(text=text)


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


async def _collect(provider, *args, **kwargs) -> list[str]:
    return [c.text async for c in with_retry_stream(provider, *args, **kwargs)]


class TestWithRetryStream:
    @pytest.mark.asyncio
    async def test_chunks_pass_through(self):
        provider = _MockProvider([["a", "b"]])
        assert await _collect(provider) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_retries_before_the_first_chunk(self):
        provider = _MockProvider([_rate("429"), _rate("429"), ["ok"]])
        assert await _collect(provider, max_retries=3) == ["ok"]

    @pytest.mark.asyncio
    async def test_error_after_the_first_chunk_is_not_replayed(self):
        """A stream cannot be replayed — a caller has already seen the chunks."""

        class Halfway(_MockProvider):
            async def stream(self, *args, **kwargs):
                yield Chunk(text="partial")
                raise _rate("429")

        with pytest.raises(_rate):
            await _collect(Halfway([]), max_retries=3)

    @pytest.mark.asyncio
    async def test_non_retriable_error_propagates_immediately(self):
        provider = _MockProvider([_auth("bad key")])
        with pytest.raises(_auth):
            await _collect(provider, max_retries=3)

    @pytest.mark.asyncio
    async def test_retries_are_exhausted(self):
        provider = _MockProvider([_rate("429")] * 3)
        with pytest.raises(_rate):
            await _collect(provider, max_retries=2)

    @pytest.mark.asyncio
    async def test_cancellation_is_never_retried(self):
        provider = _MockProvider([asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await _collect(provider, max_retries=3)

    @pytest.mark.asyncio
    async def test_arguments_are_forwarded(self):
        seen = []

        class Recorder(_MockProvider):
            async def stream(self, *args, **kwargs):
                seen.append(args)
                yield Chunk(text="ok")

        await _collect(Recorder([]), "a", "b")
        assert seen == [("a", "b")]

    @pytest.mark.asyncio
    async def test_an_empty_stream_is_not_an_error(self):
        provider = _MockProvider([[]])
        assert await _collect(provider) == []


class TestComputeDelay:
    def test_grows_then_caps(self):
        assert _compute_delay(0) < _compute_delay(1) < _compute_delay(2)
        assert 1.0 <= _compute_delay(0) <= 1.5  # base plus at most the jitter
        assert _compute_delay(20) <= 60.0

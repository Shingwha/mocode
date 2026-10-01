"""Retry semantics — the window closes at the first chunk; the policy says
how hard to try."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mocode.core.provider import Chunk, RetryPolicy, _compute_delay, with_retry_stream

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


class _PolicyProvider(_MockProvider):
    """A provider that declares its own retry policy."""

    def __init__(self, attempts: list, policy: RetryPolicy):
        super().__init__(attempts)
        self.retry_policy = policy


def _rate_limited(retry_after):
    """A retriable error whose failed response carries a Retry-After header."""
    exc = _rate("429")
    exc.response = SimpleNamespace(headers={"Retry-After": retry_after})
    return exc


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", mock)
    return mock


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
        assert await _collect(provider, policy=RetryPolicy(max_attempts=4)) == ["ok"]

    @pytest.mark.asyncio
    async def test_error_after_the_first_chunk_is_not_replayed(self):
        """A stream cannot be replayed — a caller has already seen the chunks."""

        class Halfway(_MockProvider):
            async def stream(self, *args, **kwargs):
                yield Chunk(text="partial")
                raise _rate("429")

        with pytest.raises(_rate):
            await _collect(Halfway([]), policy=RetryPolicy(max_attempts=4))

    @pytest.mark.asyncio
    async def test_non_retriable_error_propagates_immediately(self):
        provider = _MockProvider([_auth("bad key")])
        with pytest.raises(_auth):
            await _collect(provider, policy=RetryPolicy(max_attempts=4))

    @pytest.mark.asyncio
    async def test_retries_are_exhausted(self):
        provider = _MockProvider([_rate("429")] * 3)
        with pytest.raises(_rate):
            await _collect(provider, policy=RetryPolicy(max_attempts=3))

    @pytest.mark.asyncio
    async def test_cancellation_is_never_retried(self):
        provider = _MockProvider([asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await _collect(provider, policy=RetryPolicy(max_attempts=4))

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


class TestPolicyResolution:
    @pytest.mark.asyncio
    async def test_explicit_policy_beats_the_provider_one(self):
        # The provider allows only 2 attempts, the explicit argument 3 — the
        # script needs exactly 3, so success proves the argument won.
        provider = _PolicyProvider(
            [_rate("429"), _rate("429"), ["ok"]], RetryPolicy(max_attempts=2)
        )
        assert await _collect(
            provider, policy=RetryPolicy(max_attempts=3)
        ) == ["ok"]

    @pytest.mark.asyncio
    async def test_provider_policy_beats_the_default(self):
        provider = _PolicyProvider([_rate("429")] * 2, RetryPolicy(max_attempts=2))
        with pytest.raises(_rate):
            await _collect(provider)

    @pytest.mark.asyncio
    async def test_undeclared_policy_defaults_to_seven_attempts(self):
        # _MockProvider declares no retry_policy at all: the kernel default
        # allows exactly 7 attempts — six failures then success.
        provider = _MockProvider([_rate("429")] * 6 + [["ok"]])
        assert await _collect(provider) == ["ok"]

        provider = _MockProvider([_rate("429")] * 7)
        with pytest.raises(_rate):
            await _collect(provider)

    @pytest.mark.asyncio
    async def test_exhaustion_raises_the_original_exception(self):
        final = _rate("429")
        provider = _MockProvider([_rate("429"), _rate("429"), final])
        with pytest.raises(_rate) as caught:
            await _collect(provider, policy=RetryPolicy(max_attempts=3))
        assert caught.value is final


class TestRetryAfter:
    @pytest.mark.asyncio
    async def test_numeric_retry_after_is_slept_over_backoff(self, _patch_sleep):
        # Defaults would sleep somewhere in [1.0, 1.5]; 0.3 can only come
        # from the header.
        provider = _MockProvider([_rate_limited("0.3"), ["ok"]])
        assert await _collect(provider) == ["ok"]
        assert _patch_sleep.call_args_list[0].args == (0.3,)

    @pytest.mark.asyncio
    async def test_header_lookup_is_case_insensitive(self, _patch_sleep):
        exc = _rate("429")
        exc.response = SimpleNamespace(headers={"retry-after": "0.7"})
        provider = _MockProvider([exc, ["ok"]])
        assert await _collect(provider) == ["ok"]
        assert _patch_sleep.call_args_list[0].args == (0.7,)

    @pytest.mark.asyncio
    async def test_raw_numeric_header_values(self, _patch_sleep):
        provider = _MockProvider([_rate_limited(2), ["ok"]])
        assert await _collect(provider) == ["ok"]
        assert _patch_sleep.call_args_list[0].args == (2.0,)

    @pytest.mark.asyncio
    async def test_http_date_falls_back_to_exponential_backoff(self, _patch_sleep):
        provider = _MockProvider(
            [_rate_limited("Wed, 21 Oct 2015 07:28:00 GMT"), ["ok"]]
        )
        assert await _collect(provider) == ["ok"]
        (delay,) = _patch_sleep.call_args_list[0].args
        assert 1.0 <= delay <= 1.5

    @pytest.mark.asyncio
    async def test_honor_retry_after_false_ignores_the_header(self, _patch_sleep):
        provider = _MockProvider([_rate_limited("0.3"), ["ok"]])
        policy = RetryPolicy(max_attempts=2, honor_retry_after=False)
        assert await _collect(provider, policy=policy) == ["ok"]
        (delay,) = _patch_sleep.call_args_list[0].args
        assert 1.0 <= delay <= 1.5

    @pytest.mark.asyncio
    async def test_missing_response_falls_back_to_backoff(self, _patch_sleep):
        provider = _MockProvider([_rate("429"), ["ok"]])
        assert await _collect(provider) == ["ok"]
        (delay,) = _patch_sleep.call_args_list[0].args
        assert 1.0 <= delay <= 1.5


class TestBackoffBounds:
    @pytest.mark.asyncio
    async def test_each_delay_is_its_step_plus_at_most_jitter(self, _patch_sleep):
        provider = _MockProvider([_rate("429")] * 3 + [["ok"]])
        assert await _collect(provider) == ["ok"]
        delays = [call.args[0] for call in _patch_sleep.call_args_list]
        for step, delay in zip([1.0, 2.0, 4.0], delays):
            assert step <= delay <= step + 0.5

    @pytest.mark.asyncio
    async def test_delay_caps_at_max_delay(self, _patch_sleep):
        provider = _MockProvider([_rate("429")] * 2 + [["ok"]])
        policy = RetryPolicy(base_delay=2.0, max_delay=3.0)
        assert await _collect(provider, policy=policy) == ["ok"]
        delays = [call.args[0] for call in _patch_sleep.call_args_list]
        assert 2.0 <= delays[0] <= 2.5
        assert delays[1] == 3.0


class TestComputeDelay:
    def test_grows_then_caps(self):
        assert _compute_delay(0) < _compute_delay(1) < _compute_delay(2)
        assert 1.0 <= _compute_delay(0) <= 1.5  # base plus at most the jitter
        assert _compute_delay(20) <= 60.0

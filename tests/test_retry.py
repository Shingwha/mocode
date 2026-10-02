"""Retry semantics — the window closes at the first chunk; the policy says
how hard to try."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import mocode.core.provider as provider_module
from mocode.core.provider import (
    Chunk,
    RetryDeadlineExceeded,
    RetryPolicy,
    _compute_delay,
    with_retry_stream,
)
from mocode.testing import MockProvider, say

_rate = type("RateLimitError", (Exception,), {})
_auth = type("AuthenticationError", (Exception,), {})


def _retriable(exc: Exception) -> bool:
    """What a rate-limit-shaped backend retries — and nothing else."""
    return isinstance(exc, _rate)


def _provider(
    *attempts,
    policy: RetryPolicy | None = None,
    clock: "_Clock | None" = None,
    burn: float = 0.0,
) -> MockProvider:
    """A scripted provider with the retry classification these tests need.

    *attempts* are script entries: :func:`say` answers or exceptions to raise.
    A failing attempt may consume simulated wall clock (*clock* + *burn*) —
    what a real call costs before it fails.
    """
    burn_clock = (
        {"on_attempt": lambda: setattr(clock, "now", clock.now + burn)}
        if clock is not None
        else {}
    )
    provider = MockProvider(list(attempts), retriable=_retriable, **burn_clock)
    if policy is not None:
        provider.retry_policy = policy
    return provider


def _rate_limited(retry_after):
    """A retriable error whose failed response carries a Retry-After header."""
    exc = _rate("429")
    exc.response = SimpleNamespace(headers={"Retry-After": retry_after})
    return exc


class _Clock:
    """A fake monotonic clock the test advances by hand."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now


class _Halfway(MockProvider):
    """A stream that fails after its first chunk — unreplayable by contract."""

    async def stream(self, *args, **kwargs):
        yield Chunk(text="partial")
        raise _rate("429")


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", mock)
    return mock


#: The request shape a provider's stream is called with — a retry test cares
#: about the chunk stream, not what was asked for.
_REQUEST = ([], "sys", [], None, None)


async def _collect(provider, *args, **kwargs) -> str:
    """The text a (possibly retried) stream delivered, joined end to end."""
    texts = [
        chunk.text
        async for chunk in with_retry_stream(
            provider, *(args or _REQUEST), **kwargs
        )
    ]
    return "".join(texts)


class TestWithRetryStream:
    async def test_chunks_pass_through(self):
        provider = _provider(say("ab"))
        assert await _collect(provider) == "ab"

    async def test_retries_before_the_first_chunk(self):
        provider = _provider(_rate("429"), _rate("429"), say("ok"))
        assert await _collect(provider, policy=RetryPolicy(max_attempts=4)) == "ok"

    async def test_error_after_the_first_chunk_is_not_replayed(self):
        """A stream cannot be replayed — a caller has already seen the chunks."""
        with pytest.raises(_rate):
            await _collect(_Halfway(), policy=RetryPolicy(max_attempts=4))

    async def test_non_retriable_error_propagates_immediately(self):
        provider = _provider(_auth("bad key"))
        with pytest.raises(_auth):
            await _collect(provider, policy=RetryPolicy(max_attempts=4))

    async def test_retries_are_exhausted(self):
        provider = _provider(_rate("429"), _rate("429"), _rate("429"))
        with pytest.raises(_rate):
            await _collect(provider, policy=RetryPolicy(max_attempts=3))

    async def test_cancellation_is_never_retried(self):
        provider = _provider(asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await _collect(provider, policy=RetryPolicy(max_attempts=4))

    async def test_arguments_are_forwarded(self):
        provider = _provider()

        await _collect(provider, ["m"], "s", None, None, None)

        assert provider.last_request == {
            "messages": ["m"],
            "system": "s",
            "tools": None,
            "max_tokens": None,
            "effort": None,
        }

    async def test_an_empty_stream_is_not_an_error(self):
        provider = _provider(say(""))
        assert await _collect(provider) == ""


class TestPolicyResolution:
    async def test_explicit_policy_beats_the_provider_one(self):
        # The provider allows only 2 attempts, the explicit argument 3 — the
        # script needs exactly 3, so success proves the argument won.
        provider = _provider(
            _rate("429"), _rate("429"), say("ok"), policy=RetryPolicy(max_attempts=2)
        )
        assert await _collect(
            provider, policy=RetryPolicy(max_attempts=3)
        ) == "ok"

    async def test_provider_policy_beats_the_default(self):
        provider = _provider(
            _rate("429"), _rate("429"), policy=RetryPolicy(max_attempts=2)
        )
        with pytest.raises(_rate):
            await _collect(provider)

    async def test_undeclared_policy_defaults_to_seven_attempts(self):
        # A provider that declares no retry_policy at all gets the kernel
        # default: exactly 7 attempts — six failures then success.
        provider = _provider(*[_rate("429")] * 6, say("ok"))
        assert await _collect(provider) == "ok"

        provider = _provider(*[_rate("429")] * 7)
        with pytest.raises(_rate):
            await _collect(provider)

    async def test_exhaustion_raises_the_original_exception(self):
        final = _rate("429")
        provider = _provider(_rate("429"), _rate("429"), final)
        with pytest.raises(_rate) as caught:
            await _collect(provider, policy=RetryPolicy(max_attempts=3))
        assert caught.value is final


class TestRetryAfter:
    async def test_numeric_retry_after_is_slept_over_backoff(self, _patch_sleep):
        # Defaults would sleep somewhere in [1.0, 1.5]; 0.3 can only come
        # from the header.
        provider = _provider(_rate_limited("0.3"), say("ok"))
        assert await _collect(provider) == "ok"
        assert _patch_sleep.call_args_list[0].args == (0.3,)

    async def test_header_lookup_is_case_insensitive(self, _patch_sleep):
        exc = _rate("429")
        exc.response = SimpleNamespace(headers={"retry-after": "0.7"})
        provider = _provider(exc, say("ok"))
        assert await _collect(provider) == "ok"
        assert _patch_sleep.call_args_list[0].args == (0.7,)

    async def test_raw_numeric_header_values(self, _patch_sleep):
        provider = _provider(_rate_limited(2), say("ok"))
        assert await _collect(provider) == "ok"
        assert _patch_sleep.call_args_list[0].args == (2.0,)

    async def test_http_date_falls_back_to_exponential_backoff(self, _patch_sleep):
        provider = _provider(
            _rate_limited("Wed, 21 Oct 2015 07:28:00 GMT"), say("ok")
        )
        assert await _collect(provider) == "ok"
        (delay,) = _patch_sleep.call_args_list[0].args
        assert 1.0 <= delay <= 1.5

    async def test_honor_retry_after_false_ignores_the_header(self, _patch_sleep):
        provider = _provider(_rate_limited("0.3"), say("ok"))
        policy = RetryPolicy(max_attempts=2, honor_retry_after=False)
        assert await _collect(provider, policy=policy) == "ok"
        (delay,) = _patch_sleep.call_args_list[0].args
        assert 1.0 <= delay <= 1.5

    async def test_missing_response_falls_back_to_backoff(self, _patch_sleep):
        provider = _provider(_rate("429"), say("ok"))
        assert await _collect(provider) == "ok"
        (delay,) = _patch_sleep.call_args_list[0].args
        assert 1.0 <= delay <= 1.5


class TestBackoffBounds:
    async def test_each_delay_is_its_step_plus_at_most_jitter(self, _patch_sleep):
        provider = _provider(*[_rate("429")] * 3, say("ok"))
        assert await _collect(provider) == "ok"
        delays = [call.args[0] for call in _patch_sleep.call_args_list]
        for step, delay in zip([1.0, 2.0, 4.0], delays):
            assert step <= delay <= step + 0.5

    async def test_delay_caps_at_max_delay(self, _patch_sleep):
        provider = _provider(*[_rate("429")] * 2, say("ok"))
        policy = RetryPolicy(base_delay=2.0, max_delay=3.0)
        assert await _collect(provider, policy=policy) == "ok"
        delays = [call.args[0] for call in _patch_sleep.call_args_list]
        assert 2.0 <= delays[0] <= 2.5
        assert delays[1] == 3.0


class TestComputeDelay:
    def test_grows_then_caps(self):
        assert _compute_delay(0) < _compute_delay(1) < _compute_delay(2)
        assert 1.0 <= _compute_delay(0) <= 1.5  # base plus at most the jitter
        assert _compute_delay(20) <= 60.0


class TestDeadline:
    """The wall clock bounds the orchestration itself — no sleep or retry
    past it, and a Retry-After never overrides the budget."""

    async def test_no_deadline_ignores_the_clock(self, monkeypatch, _patch_sleep):
        # Without a deadline the clock is never consulted, however late it
        # reads — the default behavior is exactly as it was.
        clock = _Clock(start=10**9)
        monkeypatch.setattr(provider_module, "time", clock)
        provider = _provider(_rate("429"), say("ok"))
        assert await _collect(provider) == "ok"
        assert _patch_sleep.call_count == 1

    async def test_deadline_already_gone_stops_before_the_first_attempt(
        self, monkeypatch, _patch_sleep
    ):
        clock = _Clock(start=100.0)
        monkeypatch.setattr(provider_module, "time", clock)
        provider = _provider(say("ok"))  # would have succeeded
        with pytest.raises(RetryDeadlineExceeded) as caught:
            await _collect(provider, deadline=99.0)
        assert provider.calls == []  # no attempt was even made
        assert _patch_sleep.call_count == 0
        assert caught.value.provider == "mock"
        assert caught.value.last_error is None

    async def test_deadline_passing_during_the_sleep_stops_the_next_attempt(
        self, monkeypatch, _patch_sleep
    ):
        # The backoff sleep is what consumes the rest of the budget; the
        # next attempt top finds the deadline behind it and stops.
        clock = _Clock()
        monkeypatch.setattr(provider_module, "time", clock)
        _patch_sleep.side_effect = lambda seconds: setattr(
            clock, "now", clock.now + seconds
        )
        provider = _provider(_rate("429"), say("ok"))
        policy = RetryPolicy(base_delay=2.0, jitter=0.0)
        with pytest.raises(RetryDeadlineExceeded):
            await _collect(provider, deadline=2.0, policy=policy)
        assert _patch_sleep.call_count == 1  # slept once, never retried
        assert provider.responses == [say("ok")]

    async def test_retry_after_yields_to_the_deadline(
        self, monkeypatch, _patch_sleep
    ):
        """The budget is the hard boundary: a server-stated wait cannot buy
        a sleep past it."""
        clock = _Clock()
        monkeypatch.setattr(provider_module, "time", clock)
        # The failing attempt itself burns the last of the budget.
        provider = _provider(
            _rate_limited("30.0"), say("ok"), clock=clock, burn=1.0
        )
        with pytest.raises(RetryDeadlineExceeded) as caught:
            await _collect(provider, deadline=1.0)
        assert _patch_sleep.call_count == 0  # the 30s wait never slept
        assert caught.value.provider == "mock"
        assert isinstance(caught.value.last_error, _rate)

    async def test_deadline_still_future_allows_the_retry(
        self, monkeypatch, _patch_sleep
    ):
        # A deadline that has not passed changes nothing about the attempt
        # it still covers — the burn lands inside the budget, the retry runs.
        clock = _Clock()
        monkeypatch.setattr(provider_module, "time", clock)
        provider = _provider(
            _rate_limited("0.3"), say("ok"), clock=clock, burn=1.0
        )
        assert await _collect(provider, deadline=10.0) == "ok"
        assert _patch_sleep.call_args_list[0].args == (0.3,)  # Retry-After honored

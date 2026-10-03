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


def _rate_limited(retry_after, header: str = "Retry-After"):
    """A retriable error whose failed response carries a Retry-After header."""
    exc = _rate("429")
    exc.response = SimpleNamespace(headers={header: retry_after})
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
    async def test_the_stream_is_passed_through_verbatim(self):
        # 空流也是一个合法回答，不是错误。
        assert await _collect(_provider(say("ab"))) == "ab"
        assert await _collect(_provider(say(""))) == ""

    async def test_retries_before_the_first_chunk(self):
        provider = _provider(_rate("429"), _rate("429"), say("ok"))
        assert await _collect(provider, policy=RetryPolicy(max_attempts=4)) == "ok"

    async def test_error_after_the_first_chunk_is_not_replayed(self):
        """A stream cannot be replayed — a caller has already seen the chunks."""
        with pytest.raises(_rate):
            await _collect(_Halfway(), policy=RetryPolicy(max_attempts=4))

    async def test_an_exception_the_policy_does_not_retry_propagates_at_once(
        self, _patch_sleep
    ):
        """非 retriable 与取消都不进退避：立即上溯，一次入睡都没有。"""
        for exc in (_auth("bad key"), asyncio.CancelledError()):
            provider = _provider(exc)
            with pytest.raises(type(exc)):
                await _collect(provider, policy=RetryPolicy(max_attempts=4))
            assert len(provider.calls) == 1  # only one attempt was made
            assert _patch_sleep.call_count == 0  # and no backoff was slept

    async def test_retries_are_exhausted(self):
        provider = _provider(_rate("429"), _rate("429"), _rate("429"))
        with pytest.raises(_rate):
            await _collect(provider, policy=RetryPolicy(max_attempts=3))

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
    """Retry-After：数字头被 honored 时按它睡，其余退回指数退避。

    入睡值不钉字面量，只钉区间：honored 的值来自头本身，且与默认退避
    区间互斥；解析不了与关闭 honoring 都落在退避区间里。
    """

    @pytest.mark.parametrize(
        "header,retry_after,policy,window,honored",
        [
            ("Retry-After", "0.3", None, (0.25, 0.35), True),
            ("retry-after", "0.7", None, (0.65, 0.75), True),  # 头名大小写不敏感
            ("Retry-After", 2, None, (1.95, 2.05), True),  # 原生数字值
            # HTTP-date 解析不出秒数，退回指数退避。
            ("Retry-After", "Wed, 21 Oct 2015 07:28:00 GMT", None, (1.0, 1.5), False),
            # 策略关掉 honoring，头有值也当没看见。
            (
                "Retry-After",
                "0.3",
                RetryPolicy(max_attempts=2, honor_retry_after=False),
                (1.0, 1.5),
                False,
            ),
        ],
    )
    async def test_a_numeric_header_is_slept_instead_of_backoff(
        self, header, retry_after, policy, window, honored, _patch_sleep
    ):
        provider = _provider(_rate_limited(retry_after, header=header), say("ok"))
        assert await _collect(provider, policy=policy) == "ok"
        (delay,) = _patch_sleep.call_args_list[0].args
        lo, hi = window
        assert lo <= delay <= hi
        if honored:
            # 头 honored 时的值只能来自头，不落在默认退避区间。
            assert not 1.0 <= delay <= 1.5

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

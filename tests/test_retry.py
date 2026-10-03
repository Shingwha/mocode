"""Retry semantics — the window closes at the first chunk; the policy says
how hard to try."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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

from .conftest import FakeClock, advance

_rate = type("RateLimitError", (Exception,), {})
_auth = type("AuthenticationError", (Exception,), {})


def _retriable(exc: Exception) -> bool:
    """What a rate-limit-shaped backend retries — and nothing else."""
    return isinstance(exc, _rate)


def _provider(
    *attempts,
    policy: RetryPolicy | None = None,
    clock: FakeClock | None = None,
    burn: float = 0.0,
) -> MockProvider:
    """A scripted provider with the retry classification these tests need.

    *attempts* are script entries: :func:`say` answers or exceptions to raise.
    A failing attempt may consume simulated wall clock (*clock* + *burn*) —
    what a real call costs before it fails.
    """
    burn_clock = (
        {"on_attempt": lambda: advance(clock, burn)}
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


class _Halfway(MockProvider):
    """A stream that fails after its first chunk — unreplayable by contract."""

    async def stream(self, *args, **kwargs):
        yield Chunk(text="partial")
        raise _rate("429")


class _BackoffRecorder:
    """被测编排的退避入睡：不睡，只记账，并把时间在假时钟上推过去。

    它替换的是被测行为（退避）本身，不是测试等待——``asyncio.sleep``
    在本文件里从不作为同步手段出现。每次入睡记下秒数供区间断言；"睡着
    的时间流逝了"由 :func:`advance` 表达，于是截止判断在假时间上成立，
    与真睡消耗预算的方式一致。
    """

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.calls: list[float] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        advance(self.clock, seconds)


@pytest.fixture(autouse=True)
def backoff(monkeypatch) -> _BackoffRecorder:
    """把退避入睡与 provider 时钟都落到 conftest 的 FakeClock 上。

    被测编排读 ``provider_module.time.monotonic()`` 做截止判断——把它指向
    同一枚假时钟，"入睡消耗预算"与"截止判断"于是读同一个 now。
    """
    clock = FakeClock()
    recorder = _BackoffRecorder(clock)
    monkeypatch.setattr(asyncio, "sleep", recorder)
    monkeypatch.setattr(provider_module, "time", clock)
    return recorder


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
    async def test_the_retry_window_closes_at_the_first_chunk(self):
        """窗口的全貌：成功原样穿过（空流也合法），首块前可重试，首块后不可。"""
        assert await _collect(_provider(say("ab"))) == "ab"
        assert await _collect(_provider(say(""))) == ""

        # 首块之前的失败可以重试：两次 429 之后成功，文本原样交付。
        provider = _provider(_rate("429"), _rate("429"), say("ok"))
        assert await _collect(provider, policy=RetryPolicy(max_attempts=4)) == "ok"

        # A stream cannot be replayed — a caller has already seen the chunks.
        with pytest.raises(_rate):
            await _collect(_Halfway(), policy=RetryPolicy(max_attempts=4))

    async def test_an_exception_the_policy_does_not_retry_propagates_at_once(
        self, backoff
    ):
        """非 retriable 与取消都不进退避：立即上溯，一次入睡都没有。"""
        for exc in (_auth("bad key"), asyncio.CancelledError()):
            provider = _provider(exc)
            with pytest.raises(type(exc)):
                await _collect(provider, policy=RetryPolicy(max_attempts=4))
            assert len(provider.calls) == 1  # only one attempt was made
            assert backoff.call_count == 0  # and no backoff was slept

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

    async def test_the_provider_policy_beats_the_default_of_seven_attempts(self):
        """没声明策略的 provider 吃内核默认（正好 7 次）；声明了的压过默认。"""
        # A provider that declares no retry_policy at all gets the kernel
        # default: exactly 7 attempts — six failures then success.
        provider = _provider(*[_rate("429")] * 6, say("ok"))
        assert await _collect(provider) == "ok"

        exhausted = _provider(*[_rate("429")] * 7)
        with pytest.raises(_rate):
            await _collect(exhausted)

        # 声明了 2 次上限的 provider：第二次失败即止，不等默认的 7 次。
        capped = _provider(
            _rate("429"), _rate("429"), policy=RetryPolicy(max_attempts=2)
        )
        with pytest.raises(_rate):
            await _collect(capped)

    async def test_exhaustion_raises_the_original_exception(self):
        """重试花光：上溯的就是最后一次那个原始异常，不是包装。"""
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
        self, header, retry_after, policy, window, honored, backoff
    ):
        provider = _provider(_rate_limited(retry_after, header=header), say("ok"))
        assert await _collect(provider, policy=policy) == "ok"
        delay = backoff.calls[0]
        lo, hi = window
        assert lo <= delay <= hi
        if honored:
            # 头 honored 时的值只能来自头，不落在默认退避区间。
            assert not 1.0 <= delay <= 1.5


class TestBackoffBounds:
    async def test_the_backoff_grows_by_steps_and_caps(self, backoff):
        """退避曲线的两个面：编排真睡的值，与纯函数的形状，是同一条曲线。"""
        provider = _provider(*[_rate("429")] * 3, say("ok"))
        assert await _collect(provider) == "ok"
        # 默认策略：每一步是基数翻倍，至多加抖动。
        for step, delay in zip([1.0, 2.0, 4.0], backoff.calls):
            assert step <= delay <= step + 0.5

        # 纯函数面：同一曲线单调增长，默认封顶 60。
        assert _compute_delay(0) < _compute_delay(1) < _compute_delay(2)
        assert 1.0 <= _compute_delay(0) <= 1.5  # base plus at most the jitter
        assert _compute_delay(20) <= 60.0

        # 显式策略：基数 2、抖动 0，翻倍后顶到 max_delay 的 3.0。
        slept = len(backoff.calls)
        capped = _provider(*[_rate("429")] * 2, say("ok"))
        policy = RetryPolicy(base_delay=2.0, jitter=0.0, max_delay=3.0)
        assert await _collect(capped, policy=policy) == "ok"
        assert backoff.calls[slept] == 2.0
        assert backoff.calls[slept + 1] == 3.0


class TestDeadline:
    """The wall clock bounds the orchestration itself — no sleep or retry
    past it, and a Retry-After never overrides the budget."""

    async def test_the_deadline_switch(self, backoff):
        """没有截止：时钟读多晚都不管；截止已过：一次尝试都不发。"""
        # Without a deadline the clock is never consulted, however late it
        # reads — the default behavior is exactly as it was.
        advance(backoff.clock, 10**9)  # the clock reads very late
        provider = _provider(_rate("429"), say("ok"))
        assert await _collect(provider) == "ok"
        assert backoff.call_count == 1

        # 时钟此刻已在截止之后：第一次尝试都不发，异常带着现场。
        late = _provider(say("ok"))  # would have succeeded
        with pytest.raises(RetryDeadlineExceeded) as caught:
            await _collect(late, deadline=99.0)
        assert late.calls == []  # no attempt was even made
        assert backoff.call_count == 1  # and still no backoff was slept
        assert caught.value.provider == "mock"
        assert caught.value.last_error is None

    async def test_deadline_passing_during_the_sleep_stops_the_next_attempt(
        self, backoff
    ):
        # The backoff sleep is what consumes the rest of the budget; the
        # next attempt top finds the deadline behind it and stops.
        provider = _provider(_rate("429"), say("ok"))
        policy = RetryPolicy(base_delay=2.0, jitter=0.0)
        with pytest.raises(RetryDeadlineExceeded):
            await _collect(provider, deadline=2.0, policy=policy)
        assert backoff.call_count == 1  # slept once, never retried
        assert provider.responses == [say("ok")]

    async def test_the_budget_bounds_a_stated_wait(self, backoff):
        """预算是硬边界：服务器声明的等待买不过它；没到的截止不改变什么。"""
        # The failing attempt itself burns the last of the budget, so the
        # 30s stated wait never sleeps.
        burned = _provider(
            _rate_limited("30.0"), say("ok"), clock=backoff.clock, burn=1.0
        )
        with pytest.raises(RetryDeadlineExceeded) as caught:
            await _collect(burned, deadline=1.0)
        assert backoff.call_count == 0  # the 30s wait never slept
        assert caught.value.provider == "mock"
        assert isinstance(caught.value.last_error, _rate)

        # A deadline that has not passed changes nothing about the attempt
        # it still covers — the burn lands inside the budget, the retry runs
        # and the stated wait is honored.
        honored = _provider(
            _rate_limited("0.3"), say("ok"), clock=backoff.clock, burn=1.0
        )
        assert await _collect(honored, deadline=10.0) == "ok"
        assert 0.25 <= backoff.calls[0] <= 0.35  # Retry-After honored

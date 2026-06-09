"""Tests for retry — exponential backoff on transient LLM errors."""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from mocode.core.provider import _compute_delay, with_retry


# ---- Helpers: fake openai error types + mock provider ----

def _make_openai_errors():
    """Create minimal stand-ins for openai SDK error classes."""
    rate = type("RateLimitError", (Exception,), {})
    internal = type("InternalServerError", (Exception,), {})
    conn = type("APIConnectionError", (Exception,), {})
    timeout = type("APITimeoutError", (Exception,), {})
    auth = type("AuthenticationError", (Exception,), {})
    return rate, internal, conn, timeout, auth


_rate, _internal, _conn, _timeout, _auth = _make_openai_errors()


class _MockProvider:
    """Mock provider with is_retriable that mimics OpenAI error classification."""

    @property
    def model(self) -> str:
        return "mock"

    def is_retriable(self, exc: Exception) -> bool:
        return isinstance(exc, (_rate, _internal, _conn, _timeout))

    async def call(self, **kwargs):
        raise NotImplementedError


_provider = _MockProvider()


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch):
    """Mock asyncio.sleep so retry tests run instantly."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


# ---- Tests ----


class TestWithRetry:
    """Tests for the with_retry async function."""

    @pytest.mark.asyncio
    async def test_success_first_try(self):
        """Function succeeds immediately — no retry."""
        fn = AsyncMock(return_value="ok")
        result = await with_retry(_provider, fn, max_retries=3)
        assert result == "ok"
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retries(self):
        """Function fails with retriable error, then succeeds."""
        fn = AsyncMock(side_effect=[_rate("429"), _rate("429"), "ok"])
        result = await with_retry(_provider, fn, max_retries=3)
        assert result == "ok"
        assert fn.call_count == 3

    @pytest.mark.asyncio
    async def test_non_retriable_error_propagates(self):
        """Non-retriable error raises immediately, no retry."""
        fn = AsyncMock(side_effect=_auth("bad key"))
        with pytest.raises(_auth, match="bad key"):
            await with_retry(_provider, fn, max_retries=3)
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """CancelledError propagates immediately, no retry."""
        fn = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await with_retry(_provider, fn, max_retries=3)
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        """All retries used up — raises the last retriable error."""
        fn = AsyncMock(side_effect=_rate("429"))
        with pytest.raises(_rate):
            await with_retry(_provider, fn, max_retries=6)
        # 1 initial + 6 retries = 7 total attempts
        assert fn.call_count == 7

    @pytest.mark.asyncio
    async def test_with_retry_passes_args(self):
        """Arguments and keyword arguments are forwarded correctly."""
        fn = AsyncMock(return_value="result")
        result = await with_retry(_provider, fn, "a", "b", max_retries=2, key="val")
        assert result == "result"
        fn.assert_called_once_with("a", "b", key="val")


class TestComputeDelay:
    """Tests for _compute_delay exponential backoff."""

    def test_delay_grows_exponentially(self):
        """Delay should grow with attempt number."""
        d0 = _compute_delay(0)
        d1 = _compute_delay(1)
        d2 = _compute_delay(2)
        assert d0 < d1 < d2

    def test_delay_respects_base(self):
        """First attempt delay should be near BASE_DELAY (1.0)."""
        d = _compute_delay(0)
        assert 1.0 <= d <= 1.5  # base + max jitter

    def test_delay_capped(self):
        """High attempt numbers should not exceed MAX_DELAY (60s)."""
        d = _compute_delay(20)
        assert d <= 60.0

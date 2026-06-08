"""Provider Protocol + Response DTOs.

All LLM consumers only touch Response/ToolCall/Usage DTOs,
never any SDK-specific types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """LLM-issued tool call."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class Usage:
    """Token usage."""

    prompt_tokens: int
    completion_tokens: int


@dataclass
class Response:
    """Normalized LLM response — all consumers only touch this type."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    reasoning_content: str | None = None


@runtime_checkable
class Provider(Protocol):
    """LLM provider protocol."""

    @property
    def model(self) -> str: ...

    async def call(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Response: ...


# ---- Retry with exponential backoff ----

import asyncio as _asyncio
import logging as _logging
import random as _random
from typing import Awaitable as _Awaitable, Callable as _Callable, TypeVar as _TypeVar

_T = _TypeVar("_T")
_retry_log = _logging.getLogger(__name__)

_MAX_RETRIES = 6        # 7 total attempts
_BASE_DELAY = 1.0       # seconds
_MAX_DELAY = 60.0       # cap
_JITTER_MAX = 0.5       # random jitter range


def _is_retriable(exc: Exception) -> bool:
    """Check if an exception is a transient/retriable error."""
    try:
        from openai import (
            RateLimitError,
            InternalServerError,
            APIConnectionError,
            APITimeoutError,
        )
        return isinstance(
            exc,
            (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError),
        )
    except ImportError:
        return False


def _compute_delay(attempt: int) -> float:
    """Exponential backoff with jitter: base * 2^attempt + jitter, capped."""
    delay = _BASE_DELAY * (2 ** attempt) + _random.uniform(0, _JITTER_MAX)
    return min(delay, _MAX_DELAY)


async def with_retry(
    fn: _Callable[..., _Awaitable[_T]],
    *args: Any,
    max_retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> _T:
    """Call an async function with retry on transient errors.

    Retries RateLimitError, InternalServerError, APIConnectionError,
    APITimeoutError with exponential backoff + jitter.
    All other exceptions propagate immediately.
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except _asyncio.CancelledError:
            raise  # Never retry user cancellation
        except Exception as exc:
            if not _is_retriable(exc) or attempt >= max_retries:
                raise
            last_exc = exc
            delay = _compute_delay(attempt)
            _retry_log.warning(
                "LLM call failed (%s), retrying in %.1fs (attempt %d/%d)",
                type(exc).__name__, delay, attempt + 1, max_retries,
            )
            await _asyncio.sleep(delay)

    raise last_exc  # unreachable, but satisfies type checker

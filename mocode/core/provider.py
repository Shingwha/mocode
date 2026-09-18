"""Provider Protocol + streaming DTOs.

Providers stream. All LLM consumers only ever touch Chunk/Response/ToolCall/
Usage — never an SDK-specific type — and the loop turns a chunk stream into the
kernel's event stream as it arrives.

A tool call is split across chunks by every streaming API: the first fragment
carries the index/id/name, later ones append JSON argument text. Accumulate by
``index``; :class:`StreamAccumulator` does it for you.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A complete LLM-issued tool call."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class ToolCallDelta:
    """One fragment of a tool call inside a stream."""

    index: int = 0
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class Usage:
    """Token usage."""

    prompt_tokens: int
    completion_tokens: int


@dataclass
class Chunk:
    """One streaming delta.

    Every field is optional: a chunk carries whatever arrived. Text and
    reasoning accumulate by concatenation, tool calls by ``index``.
    """

    text: str = ""
    reasoning: str = ""
    tool_call: ToolCallDelta | None = None
    usage: Usage | None = None
    finish_reason: str | None = None


@dataclass
class Response:
    """What a chunk stream adds up to.

    The loop builds one per iteration and reports it through the
    ``IterationFinished`` event, so a consumer that ignored the deltas can
    still read the whole response.
    """

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    reasoning_content: str | None = None


@dataclass(frozen=True)
class ModelSpec:
    """What the kernel knows about the model it is driving.

    Both limits are optional because MoCode does not invent them. An absent
    ``max_output`` means the request carries no output cap at all and the server
    applies its own — guessing low would silently truncate answers.
    """

    name: str
    context_window: int | None = None
    max_output: int | None = None


class StreamAccumulator:
    """Folds chunks into a :class:`Response`.

    Deliberately a plain object rather than a helper function: a consumer that
    renders deltas needs to accumulate them while they stream past, not after.

    Fragments join by ``index``, which the streaming contract reserves for one
    call within one response. That invariant is what makes ``id`` and ``name``
    assignable (they arrive once) while ``arguments`` concatenates (it arrives
    in pieces).
    """

    def __init__(self) -> None:
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._tool_slots: dict[int, ToolCallDelta] = {}
        self.usage: Usage | None = None
        self.finish_reason: str | None = None

    def feed(self, chunk: Chunk) -> None:
        if chunk.text:
            self._text.append(chunk.text)
        if chunk.reasoning:
            self._reasoning.append(chunk.reasoning)
        if chunk.usage is not None:
            self.usage = chunk.usage
        if chunk.finish_reason is not None:
            self.finish_reason = chunk.finish_reason

        delta = chunk.tool_call
        if delta is not None:
            slot = self._tool_slots.setdefault(delta.index, ToolCallDelta(index=delta.index))
            # id and name arrive once; arguments arrive split across chunks.
            if delta.id:
                slot.id = delta.id
            if delta.name:
                slot.name = delta.name
            slot.arguments += delta.arguments

    @property
    def text(self) -> str:
        return "".join(self._text)

    @property
    def reasoning(self) -> str:
        return "".join(self._reasoning)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(id=slot.id, name=slot.name, arguments=slot.arguments)
            for _, slot in sorted(self._tool_slots.items())
        ]

    def build(self) -> Response:
        """Finish accumulation. Call once, after the stream is exhausted."""
        tool_calls = self.tool_calls
        return Response(
            content=self.text or None,
            tool_calls=tool_calls or None,
            usage=self.usage,
            finish_reason=self.finish_reason,
            reasoning_content=self.reasoning or None,
        )


@runtime_checkable
class Provider(Protocol):
    """LLM provider protocol.

    ``stream`` is the only required call. A provider that cannot stream
    natively yields a single chunk holding the whole response — the loop does
    not care which it is.
    """

    @property
    def model(self) -> str: ...

    def is_retriable(self, exc: Exception) -> bool: ...
    def stream(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None,
    ) -> AsyncIterator[Chunk]: ...


# ---- Retry with exponential backoff ----

_retry_log = logging.getLogger(__name__)

_MAX_RETRIES = 6        # 7 total attempts
_BASE_DELAY = 1.0       # seconds
_MAX_DELAY = 60.0       # cap
_JITTER_MAX = 0.5       # random jitter range


def _compute_delay(attempt: int) -> float:
    """Exponential backoff with jitter: base * 2^attempt + jitter, capped."""
    delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, _JITTER_MAX)
    return min(delay, _MAX_DELAY)


async def with_retry_stream(
    provider: Provider,
    stream_fn: Callable[..., AsyncIterator[Chunk]],
    *args: Any,
    max_retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> AsyncIterator[Chunk]:
    """Stream chunks, retrying only until the first one arrives.

    A stream cannot be replayed: once a chunk has reached the caller, retrying
    would duplicate output. So the retry window closes at the first chunk —
    failures after that propagate, and the caller decides what to do with a
    half-received response.

    Uses ``provider.is_retriable()`` to decide what is worth retrying.
    Cancellation is never retried.
    """
    for attempt in range(max_retries + 1):
        stream = stream_fn(*args, **kwargs)
        try:
            first = await anext(stream)
        except StopAsyncIteration:
            return  # empty stream — a legitimate (if odd) response
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not provider.is_retriable(exc) or attempt >= max_retries:
                raise
            delay = _compute_delay(attempt)
            _retry_log.warning(
                "LLM call failed (%s), retrying in %.1fs (attempt %d/%d)",
                type(exc).__name__, delay, attempt + 1, max_retries,
            )
            await asyncio.sleep(delay)
            continue

        try:
            yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()
        return


__all__ = [
    "Chunk",
    "ModelSpec",
    "Provider",
    "Response",
    "StreamAccumulator",
    "ToolCall",
    "ToolCallDelta",
    "Usage",
    "with_retry_stream",
]

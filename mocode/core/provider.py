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
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


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

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class Chunk:
    """One streaming delta.

    Every field is optional: a chunk carries whatever arrived. Text and
    reasoning accumulate by concatenation, tool calls by ``index`` — and one
    chunk may carry several call fragments at once, because not every backend
    splits parallel calls into separate deltas.
    """

    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCallDelta] = field(default_factory=list)
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
        self._built = False

    def feed(self, chunk: Chunk) -> None:
        if self._built:
            raise RuntimeError("this accumulator has already built its response")
        if chunk.text:
            self._text.append(chunk.text)
        if chunk.reasoning:
            self._reasoning.append(chunk.reasoning)
        if chunk.usage is not None:
            self.usage = chunk.usage
        if chunk.finish_reason is not None:
            self.finish_reason = chunk.finish_reason

        for delta in chunk.tool_calls:
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
        self._built = True
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

    The kernel's interchange dialect is the OpenAI wire format: ``messages``
    are OpenAI role dicts (``user`` / ``assistant`` with ``tool_calls`` /
    ``tool`` with ``tool_call_id``), ``tools`` are OpenAI function schemas,
    ``system`` travels separately, and the output cap is called ``max_tokens``.
    A provider for a backend that speaks something else translates at this
    edge — the dialect is declared here rather than abstracted away, so the
    kernel has exactly one message shape to keep correct.

    All members are required. A provider that cannot stream natively
    yields a single chunk holding the whole response — the loop does not care
    which it is.
    """

    @property
    def model(self) -> str: ...

    @property
    def retry_policy(self) -> RetryPolicy: ...

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


@dataclass(frozen=True)
class RetryPolicy:
    """The policy half of retrying: how often and how long to wait.

    The orchestration half stays in the kernel (:func:`with_retry_stream`):
    only the consumer of the stream knows that the retry window closes at the
    first chunk, and only it can keep backoff sleeps cancellable. These
    numbers, however, are knowledge about a backend — an official API and a
    rate-limit-sensitive self-hosted endpoint should not share them — so each
    provider carries its own policy and an explicit caller argument wins.

    ``honor_retry_after``: when the failing response carries a numeric
    ``Retry-After`` header, that value is slept instead of the exponential
    backoff — the server said exactly when to come back.
    """

    max_attempts: int = 7           # including the first try, not just the retries
    base_delay: float = 1.0         # seconds; doubles with every failure
    max_delay: float = 60.0         # cap on any backoff sleep
    jitter: float = 0.5             # uniform [0, jitter] added to every backoff
    honor_retry_after: bool = True  # a numeric Retry-After beats exponential backoff


_DEFAULT_RETRY_POLICY = RetryPolicy()


def _resolve_retry_policy(
    provider: Provider, policy: RetryPolicy | None
) -> RetryPolicy:
    """An explicit policy beats the provider's own; an undeclared attribute
    falls back to the kernel default — so a provider (or a test double) that
    never mentions ``retry_policy`` keeps working unchanged."""
    return policy or getattr(provider, "retry_policy", None) or _DEFAULT_RETRY_POLICY


def _compute_delay(attempt: int, policy: RetryPolicy | None = None) -> float:
    """Exponential backoff with jitter: base * 2^attempt + jitter, capped.

    ``attempt`` is the number of failures so far, so the sleep before the
    n-th retry is ``base * 2**(n-1)`` plus jitter, capped at ``max_delay``.
    """
    p = policy or _DEFAULT_RETRY_POLICY
    delay = p.base_delay * (2 ** attempt) + random.uniform(0, p.jitter)
    return min(delay, p.max_delay)


async def with_retry_stream(
    provider: Provider,
    *args: Any,
    max_retries: int = _DEFAULT_RETRY_POLICY.max_attempts - 1,
) -> AsyncIterator[Chunk]:
    """Stream chunks from ``provider.stream(*args)``, retrying until the first.

    A stream cannot be replayed: once a chunk has reached the caller, retrying
    would duplicate output. So the retry window closes at the first chunk —
    failures after that propagate, and the caller decides what to do with a
    half-received response.

    Uses ``provider.is_retriable()`` to decide what is worth retrying.
    Cancellation is never retried.
    """
    for attempt in range(max_retries + 1):
        stream = provider.stream(*args)
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
    "RetryPolicy",
    "StreamAccumulator",
    "ToolCall",
    "ToolCallDelta",
    "Usage",
    "with_retry_stream",
]

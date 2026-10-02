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
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable

#: A reasoning-effort level. The series is open: ``EFFORTS`` is only the
#: default triple, config may declare arbitrary custom level names per model,
#: and a provider translates the level name verbatim onto the wire.
Effort = str

#: The default ordered effort series — three levels, low to high. A model
#: entry in config may replace it with any custom list of level names.
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high")


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
        """The two counts as a plain dict — the shape a session stores them in."""
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
    ``max_tokens`` means the request carries no output cap at all and the
    server applies its own — guessing low would silently truncate answers.

    ``efforts`` is the model's optional ordered level table for a frontend
    selector; it defaults to the kernel's three-level series and config may
    declare any custom names. ``effort`` is the level sent with the request —
    absent means the request carries no such parameter and the server decides
    entirely on its own.
    """

    name: str
    context_window: int | None = None
    max_tokens: int | None = None
    efforts: tuple[Effort, ...] = EFFORTS
    effort: Effort | None = None


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
        """Fold one chunk in. Raises once :meth:`build` has been called.

        ``usage`` and ``finish_reason`` are last-wins — a backend reports them
        on its final chunk, and repeating them is not a contradiction.
        """
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
        """Everything said so far, in arrival order."""
        return "".join(self._text)

    @property
    def reasoning(self) -> str:
        """The model's thinking so far, concatenated across chunks.

        Empty for a backend that does not separate thinking from answering.
        """
        return "".join(self._reasoning)

    @property
    def tool_calls(self) -> list[ToolCall]:
        """The calls seen so far, ordered by ``index``.

        Live view: a call whose arguments are still streaming reports what has
        arrived so far. Complete recursively: a fragment carrying only
        ``arguments`` joins the call its ``index`` already names.
        """
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
    ``system`` travels separately, the output cap is called ``max_tokens`` and
    thinking intensity is called ``effort`` — each provider translates both
    onto its wire as it sees fit. A provider for a backend that speaks
    something else translates at this edge — the dialect is declared here
    rather than abstracted away, so the kernel has exactly one message shape
    to keep correct.

    All members are required. A provider that cannot stream natively
    yields a single chunk holding the whole response — the loop does not care
    which it is.
    """

    @property
    def model(self) -> str:
        """The model this provider is wired to, as the backend spells it."""

    @property
    def retry_policy(self) -> RetryPolicy:
        """This backend's policy — how often and how long to retry."""

    def is_retriable(self, exc: Exception) -> bool:
        """Whether *exc* is worth another attempt.

        A rate limit or a transport error is; a 400 about the request body
        never improves by being asked again. Defaults to True for anything the
        type ignores, which is the safe direction: an unknown error is worth
        one more try and the attempt budget bounds the loop.
        """
        ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None,
        effort: Effort | None,
    ) -> AsyncIterator[Chunk]:
        """Yield the response as chunks, newest token first.

        Called once per provider attempt. Usage and ``finish_reason`` belong on
        the last chunk; the loop folds the stream through a
        :class:`StreamAccumulator` and never needs the whole response first.
        """
        ...


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


def _retry_after_seconds(exc: Exception) -> float | None:
    """Best-effort ``Retry-After`` extraction; None when absent or unparsable.

    Duck-typed on purpose — the kernel imports no HTTP client. SDK errors
    carry the failed response as ``exc.response`` with ``.headers`` (already
    case-insensitive on real header objects); a plain dict is scanned
    case-insensitively. Numeric seconds are returned as-is; the HTTP-date
    form is recognized only to be skipped, and the caller falls back to
    exponential backoff.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    get = getattr(headers, "get", None)
    value = get("Retry-After") if get is not None else None
    if value is None and isinstance(headers, dict):
        for key, raw in headers.items():
            if str(key).lower() == "retry-after":
                value = raw
                break
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        seconds = float(value)
    except ValueError:  # the HTTP-date form, or garbage — not ours to parse
        return None
    return max(seconds, 0.0)


class RetryDeadlineExceeded(Exception):
    """The wall-clock deadline passed while retrying — backoff stops here.

    Not a provider failure: the caller's time budget ran out while the
    orchestrator was waiting to retry, so no chunk ever reached the caller
    (this is raised only inside the retry window, which closes at the first
    chunk). A caller should treat it as the budget endgame — the same
    outcome as its own budget checkpoints — never as an error to surface or
    to retry. Carries the provider name and the exception that triggered the
    backoff (``last_error`` is None when the deadline was already gone
    before the first attempt).
    """

    def __init__(self, provider: str, last_error: Exception | None = None) -> None:
        if last_error is not None:
            detail = f"last error: {type(last_error).__name__}: {last_error}"
        else:
            detail = "no attempt had failed yet"
        super().__init__(f"time budget expired while retrying {provider} ({detail})")
        self.provider = provider
        self.last_error = last_error


async def with_retry_stream(
    provider: Provider,
    *args: Any,
    policy: RetryPolicy | None = None,
    deadline: float | None = None,
) -> AsyncIterator[Chunk]:
    """Stream chunks from ``provider.stream(*args)``, retrying until the first.

    A stream cannot be replayed: once a chunk has reached the caller, retrying
    would duplicate output. So the retry window closes at the first chunk —
    failures after that propagate, and the caller decides what to do with a
    half-received response.

    Uses ``provider.is_retriable()`` to decide what is worth retrying. The
    policy resolves as: an explicit ``policy`` argument, else the provider's
    own ``retry_policy``, else the kernel default — a provider that never
    declares one keeps working unchanged. Cancellation is never retried.

    Backoff is exponential — ``base_delay * 2**(n-1)`` plus uniform jitter,
    capped at ``max_delay``. With ``honor_retry_after`` a numeric
    ``Retry-After`` on the failing response is slept instead, exactly as the
    server stated it.

    ``deadline`` (a ``time.monotonic`` moment; ``None`` = unlimited) bounds
    the orchestration itself: it is checked at the top of every attempt and
    again before every backoff sleep. Past it nothing is slept or retried —
    :class:`RetryDeadlineExceeded` is raised, which the caller should treat
    as the budget endgame, not an error. A pending ``Retry-After`` never
    overrides the deadline: the budget is the hard boundary.
    """
    p = _resolve_retry_policy(provider, policy)
    last_error: Exception | None = None
    for attempt in range(p.max_attempts):
        if deadline is not None and time.monotonic() >= deadline:
            raise RetryDeadlineExceeded(provider.model, last_error) from last_error
        stream = provider.stream(*args)
        try:
            first = await anext(stream)
        except StopAsyncIteration:
            return  # empty stream — a legitimate (if odd) response
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if not provider.is_retriable(exc) or attempt >= p.max_attempts - 1:
                raise
            delay = _compute_delay(attempt, p)
            retry_after = _retry_after_seconds(exc) if p.honor_retry_after else None
            if retry_after is not None:
                delay = retry_after
            if deadline is not None and time.monotonic() >= deadline:
                # The budget is a hard boundary: a pending Retry-After does
                # not buy back a sleep or a retry past it.
                raise RetryDeadlineExceeded(provider.model, exc) from exc
            _retry_log.warning(
                "LLM call failed (%s), retrying in %.1fs (attempt %d/%d)",
                type(exc).__name__, delay, attempt + 1, p.max_attempts,
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
    "EFFORTS",
    "Effort",
    "ModelSpec",
    "Provider",
    "Response",
    "RetryDeadlineExceeded",
    "RetryPolicy",
    "StreamAccumulator",
    "ToolCall",
    "ToolCallDelta",
    "Usage",
    "with_retry_stream",
]

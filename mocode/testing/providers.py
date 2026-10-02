"""Scripted provider doubles — the "model" your plugin tests talk to.

``MockProvider`` replays canned :class:`Response` objects as chunk streams.
It deliberately splits tool-call arguments across chunks, exactly as a real
API does, so the accumulator path gets exercised instead of bypassed. A
scripted entry may also be an exception: it is raised when popped, so a test
can rehearse failures (rate limits, expired keys) the way a real backend
would produce them, and ``retriable`` decides which the kernel should retry.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Callable, Iterable

from mocode.core.provider import Chunk, Response, ToolCallDelta, Usage

#: Characters of JSON argument text per chunk when replaying a tool call.
ARG_FRAGMENT = 8


def response_to_chunks(response: Response, chunk_size: int = 0) -> Iterable[Chunk]:
    """Replay a canned Response as the chunk sequence a provider would send.

    Tool-call arguments are always split into ``ARG_FRAGMENT``-sized pieces so
    the ``StreamAccumulator`` joins them the way it would join a real
    backend's. ``chunk_size`` does the same to text and reasoning (0 = one
    chunk, no fragmentation). Usage and ``finish_reason`` come last, which is
    where a real API puts them.
    """
    if response.reasoning_content:
        for part in _split(response.reasoning_content, chunk_size):
            yield Chunk(reasoning=part)

    if response.content:
        for part in _split(response.content, chunk_size):
            yield Chunk(text=part)

    for index, call in enumerate(response.tool_calls or []):
        yield Chunk(
            tool_calls=[ToolCallDelta(index=index, id=call.id, name=call.name)]
        )
        for start in range(0, len(call.arguments), ARG_FRAGMENT):
            yield Chunk(
                tool_calls=[
                    ToolCallDelta(
                        index=index, arguments=call.arguments[start : start + ARG_FRAGMENT]
                    )
                ]
            )

    if response.finish_reason:
        yield Chunk(finish_reason=response.finish_reason)
    if response.usage:
        yield Chunk(usage=response.usage)


def _split(text: str, chunk_size: int) -> list[str]:
    if chunk_size <= 0:
        return [text]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


class MockProvider:
    """Streams canned responses in order and records every request.

    The last response repeats forever, so a test that does not care how many
    iterations a run takes does not have to enumerate them. An entry of the
    script may be an exception instead of a :class:`Response` — it is raised
    when popped, after the call has been recorded, exactly where a real
    provider would fail (the retry window only covers failures before the
    first chunk, so a scripted failure can only arrive there too).

    ``retriable`` answers :meth:`is_retriable` — what the kernel should retry
    (default: nothing). ``on_attempt`` runs at the top of every stream call,
    before the scripted outcome is popped: the hook a test needs when an
    attempt itself consumes something (a fake clock, a counter).
    """

    def __init__(
        self,
        responses: list[Response] | None = None,
        *,
        chunk_size: int = 0,
        model: str = "mock",
        retriable: Callable[[Exception], bool] | None = None,
        on_attempt: Callable[[], None] | None = None,
    ):
        """Script *responses* in order; the last one repeats forever.

        ``chunk_size`` fragments text and reasoning into per-chunk pieces so a
        test can drive the streaming path instead of one atomic chunk (tool-call
        arguments are always fragmented). ``model`` is what ``.model`` reports.
        ``retriable`` decides which scripted exceptions the kernel retries —
        default none, so an unknown error fails the turn rather than looping.
        ``on_attempt`` is called once per ``stream()`` before anything is popped
        or raised, which is where a fake clock or an attempt counter advances.
        """
        self.responses = list(responses or [Response(content="done", usage=Usage(1, 1))])
        self.calls: list[dict] = []
        self.chunk_size = chunk_size
        self._model = model
        self._retriable: Callable[[Exception], bool] = retriable or (
            lambda exc: False
        )
        self._on_attempt = on_attempt

    @property
    def model(self) -> str:
        """The model name this mock claims to be — never sent anywhere."""
        return self._model

    @property
    def last_request(self) -> dict | None:
        """The most recent recorded call, or ``None`` before the first one."""
        return self.calls[-1] if self.calls else None

    def is_retriable(self, exc: Exception) -> bool:
        """Whether the kernel should retry *exc* — answered by ``retriable``."""
        return self._retriable(exc)

    async def stream(
        self,
        messages,
        system,
        tools,
        max_tokens,
        effort,
    ) -> AsyncIterator[Chunk]:
        """Record the request, then yield the next scripted response.

        An exception in the script is raised here instead — the point a real
        provider would fail, inside the window the retry logic covers.
        """
        self.calls.append(
            {
                "messages": list(messages),
                "system": system,
                "tools": tools,
                "max_tokens": max_tokens,
                "effort": effort,
            }
        )
        if self._on_attempt is not None:
            self._on_attempt()
        response = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
        if isinstance(response, BaseException):
            raise response
        for chunk in response_to_chunks(response, self.chunk_size):
            yield chunk


def tool_call_response(name: str, args: str = "{}", call_id: str = "c1") -> Response:
    """A response that asks for one tool call and nothing else."""
    from mocode.core.provider import ToolCall

    return Response(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        usage=Usage(1, 1),
        finish_reason="tool_calls",
    )


def say(text: str, *, finish_reason: str = "stop") -> Response:
    """A response that answers with plain text — how a turn normally ends."""
    return Response(content=text, usage=Usage(1, 1), finish_reason=finish_reason)


def call_tool(name: str, args: dict, *, call_id: str = "c1") -> Response:
    """:func:`tool_call_response` with *args* serialised from a dict."""
    return tool_call_response(name, json.dumps(args), call_id=call_id)


class SlowProvider(MockProvider):
    """A provider whose turn never finishes on its own.

    Every ``stream()`` parks — used to rehearse cancellation: a turn started
    against it stays running until something cancels it, which is the only way
    to exercise that path deterministically. Not a "slow" provider in the
    polling sense: it does not finish eventually either.
    """

    async def stream(self, *args):
        """Park for far longer than any test wait — the turn stays cancellable."""
        import asyncio

        await asyncio.sleep(30)
        yield  # pragma: no cover - never reached

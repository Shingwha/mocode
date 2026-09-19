"""Shared streaming test doubles.

``MockProvider`` replays canned :class:`Response` objects as chunk streams.
It deliberately splits tool-call arguments across chunks, exactly as a real
API does, so the accumulator path gets exercised instead of bypassed.
"""

from __future__ import annotations

from typing import AsyncIterator, Iterable

from mocode.core.provider import Chunk, Response, ToolCallDelta, Usage

#: Characters of JSON argument text per chunk when replaying a tool call.
ARG_FRAGMENT = 8


def response_to_chunks(response: Response, chunk_size: int = 0) -> Iterable[Chunk]:
    """Replay a canned Response as the chunk sequence a provider would send."""
    if response.reasoning_content:
        for part in _split(response.reasoning_content, chunk_size):
            yield Chunk(reasoning=part)

    if response.content:
        for part in _split(response.content, chunk_size):
            yield Chunk(text=part)

    for index, call in enumerate(response.tool_calls or []):
        yield Chunk(tool_call=ToolCallDelta(index=index, id=call.id, name=call.name))
        for start in range(0, len(call.arguments), ARG_FRAGMENT):
            yield Chunk(
                tool_call=ToolCallDelta(
                    index=index, arguments=call.arguments[start : start + ARG_FRAGMENT]
                )
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
    iterations a run takes does not have to enumerate them.
    """

    def __init__(
        self,
        responses: list[Response] | None = None,
        *,
        chunk_size: int = 0,
        model: str = "mock",
    ):
        self.responses = list(responses or [Response(content="done", usage=Usage(1, 1))])
        self.calls: list[dict] = []
        self.chunk_size = chunk_size
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def is_retriable(self, exc: Exception) -> bool:
        return False

    async def stream(
        self,
        messages,
        system,
        tools,
        max_tokens,
    ) -> AsyncIterator[Chunk]:
        self.calls.append(
            {
                "messages": list(messages),
                "system": system,
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        response = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
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


class SlowProvider(MockProvider):
    """A provider whose turn never finishes on its own."""

    async def stream(self, *args):
        import asyncio

        await asyncio.sleep(30)
        yield  # pragma: no cover - never reached

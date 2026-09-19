"""OpenAI-compatible provider implementation.

Depends on the `openai` package. Install with: uv pip install "mocode[openai]"
"""

from __future__ import annotations

from functools import cache
from typing import Any, AsyncIterator

from ..core.provider import Chunk, ToolCallDelta, Usage


@cache
def _retriable_exceptions() -> tuple[type[Exception], ...]:
    """The SDK's transient failures, imported once and never at startup."""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    return (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)


class OpenAIProvider:
    """OpenAI-compatible API provider — implements Provider Protocol."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._client = None  # lazy — created on first call
        self._model = model

        # `stream_options` rides inside extra_body — token accounting is
        # configured per endpoint — and is lifted out so it is not sent twice.
        body = dict(extra_body or {})
        configured = body.pop("stream_options", None)
        self._stream_options = (
            configured if isinstance(configured, dict) else {"include_usage": True}
        )
        self._extra_body = body or None

    def _ensure_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    @property
    def model(self) -> str:
        return self._model

    def is_retriable(self, exc: Exception) -> bool:
        return isinstance(exc, _retriable_exceptions())

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None,
    ) -> AsyncIterator[Chunk]:
        openai_messages = [
            {"role": "system", "content": system},
            *self._normalize_messages(messages),
        ]

        request: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            "tools": tools or None,
            "extra_body": self._extra_body,
            "stream": True,
            "stream_options": self._stream_options,
        }
        # No cap configured → send none and let the server apply its own limit,
        # rather than capping the answer at a number MoCode made up.
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        # The await performs the request, so a rate limit or a dead connection
        # surfaces here — inside the retry window, before any chunk is handed
        # to the caller.
        raw_stream = await self._ensure_client().chat.completions.create(**request)

        async for raw in raw_stream:
            chunk = self._to_chunk(raw)
            if chunk is not None:
                yield chunk

    @staticmethod
    def _to_chunk(raw: Any) -> Chunk | None:
        """Map one SDK chunk to a kernel Chunk, or None if it carries nothing."""
        chunk = Chunk()

        usage = getattr(raw, "usage", None)
        if usage is not None:
            chunk.usage = Usage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )

        # The usage-only chunk that closes the stream has no choices at all.
        choices = getattr(raw, "choices", None) or ()
        if not choices:
            return chunk if chunk.usage is not None else None

        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None)
            chunk.text = content if isinstance(content, str) else ""
            chunk.reasoning = _reasoning_text(delta)

            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                chunk.tool_call = _tool_call_delta(tool_calls[0])

        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is not None:
            chunk.finish_reason = finish_reason

        if not (chunk.text or chunk.reasoning or chunk.tool_call):
            return chunk if (chunk.usage is not None or chunk.finish_reason) else None
        return chunk

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop tool calls the history has no answer for.

        A reloaded or hand-edited history can pair assistant ``tool_calls``
        with tool messages that are gone; endpoints reject such a list. Calls
        whose id has no matching tool message are dropped, and an assistant
        left with no calls at all loses the field.
        """
        answered = {
            m["tool_call_id"]
            for m in messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        result = []
        for msg in messages:
            calls = msg.get("tool_calls") if msg.get("role") == "assistant" else None
            if calls is None:
                result.append(msg)
                continue
            valid = [tc for tc in calls if tc.get("id") in answered]
            if len(valid) == len(calls):
                result.append(msg)
            elif valid:
                cleaned = dict(msg)
                cleaned["tool_calls"] = valid
                result.append(cleaned)
            else:
                result.append({k: v for k, v in msg.items() if k != "tool_calls"})
        return result


def _reasoning_text(delta: Any) -> str:
    """Reasoning arrives as ``reasoning_content`` on most compatible endpoints.

    Some carry it as ``reasoning`` instead, and the newest OpenAI models use
    ``reasoning`` for a structured object rather than text — accept only
    strings, so a structured field is ignored instead of stringified.
    """
    for attr in ("reasoning_content", "reasoning"):
        value = getattr(delta, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _tool_call_delta(part: Any) -> ToolCallDelta:
    """One tool call fragment. ``index`` is 0 when an endpoint omits it."""
    function = getattr(part, "function", None)
    return ToolCallDelta(
        index=getattr(part, "index", 0) or 0,
        id=getattr(part, "id", None) or "",
        name=(getattr(function, "name", None) or "") if function else "",
        arguments=(getattr(function, "arguments", None) or "") if function else "",
    )

"""OpenAI-compatible provider implementation.

Depends on the `openai` package. Install with: uv pip install "mocode[openai]"
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from ..core.provider import Chunk, ToolCallDelta, Usage


class OpenAIProvider:
    """OpenAI-compatible API provider — implements Provider Protocol."""

    # Lazy-loaded exception classes — first call triggers openai import
    _exc_classes: tuple[type[Exception], ...] | None = None

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

        # `stream_options` is accepted as a key inside extra_body, because
        # token accounting is configured per endpoint and MoCode's config
        # already has a free-form escape hatch for that. Lift it out so it is
        # not sent twice.
        body = dict(extra_body) if extra_body else {}
        self._stream_options: dict[str, Any] = {"include_usage": True}
        configured = body.pop("stream_options", None)
        if isinstance(configured, dict):
            self._stream_options = configured
        self._extra_body = body or None

    def _ensure_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def _load_exc_classes(cls) -> tuple[type[Exception], ...]:
        """Lazy-load openai exception classes (avoids SDK import at startup)."""
        if cls._exc_classes is None:
            from openai import (
                RateLimitError,
                InternalServerError,
                APIConnectionError,
                APITimeoutError,
            )
            cls._exc_classes = (
                RateLimitError,
                InternalServerError,
                APIConnectionError,
                APITimeoutError,
            )
        return cls._exc_classes

    def is_retriable(self, exc: Exception) -> bool:
        return isinstance(exc, self._load_exc_classes())

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
        """Strip empty/orphaned tool_calls from assistant messages."""
        # Quick check: if no assistant messages have tool_calls, nothing to do
        has_assistant_tool_calls = any(
            m.get("role") == "assistant" and "tool_calls" in m
            for m in messages
        )
        if not has_assistant_tool_calls:
            return messages

        # Build set of valid tool_call_ids from tool messages
        result_ids = {
            m["tool_call_id"]
            for m in messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }

        # If no valid tool_call_ids, strip all tool_calls from assistant messages
        if not result_ids:
            result = []
            for msg in messages:
                if msg.get("role") == "assistant" and "tool_calls" in msg:
                    result.append({k: v for k, v in msg.items() if k != "tool_calls"})
                else:
                    result.append(msg)
            return result

        # Normal case: filter tool_calls
        result = []
        for msg in messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                valid = [tc for tc in msg["tool_calls"] if tc.get("id") in result_ids]
                if not valid:
                    result.append({k: v for k, v in msg.items() if k != "tool_calls"})
                elif len(valid) == len(msg["tool_calls"]):
                    # All tool_calls are valid, no copy needed
                    result.append(msg)
                else:
                    cleaned = dict(msg)
                    cleaned["tool_calls"] = valid
                    result.append(cleaned)
            else:
                result.append(msg)
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

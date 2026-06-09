"""OpenAI-compatible provider implementation.

Depends on the `openai` package. Install with: uv pip install "mocode[openai]"
"""

from __future__ import annotations

from typing import Any

from ..core.provider import Response, ToolCall, Usage
from openai import (
    RateLimitError,
    InternalServerError,
    APIConnectionError,
    APITimeoutError,
)


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
        self._extra_body = extra_body

    def _ensure_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    @property
    def model(self) -> str:
        return self._model

    def is_retriable(self, exc: Exception) -> bool:
        return isinstance(
            exc,
            (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError),
        )

    async def call(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Response:
        openai_messages = [
            {"role": "system", "content": system},
            *self._normalize_messages(messages),
        ]

        raw = await self._ensure_client().chat.completions.create(
            model=self._model,
            messages=openai_messages,
            tools=tools or None,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            extra_body=self._extra_body,
        )

        choice = raw.choices[0]
        message = choice.message

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in message.tool_calls
            ]

        usage = None
        if raw.usage:
            usage = Usage(
                prompt_tokens=raw.usage.prompt_tokens or 0,
                completion_tokens=raw.usage.completion_tokens or 0,
            )

        reasoning_content = getattr(message, "reasoning_content", None)

        return Response(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.finish_reason,
            reasoning_content=reasoning_content,
        )

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



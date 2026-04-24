"""Provider Response DTO + Provider Protocol + OpenAIProvider

所有 LLM 消费者只接触 Response/ToolCall/Usage DTO，不接触任何 SDK 类型。
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from openai import AsyncOpenAI


# ---- DTO ----


@dataclass
class ToolCall:
    """LLM 发出的工具调用"""
    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class Usage:
    """Token 用量"""
    prompt_tokens: int
    completion_tokens: int


@dataclass
class Response:
    """归一化的 LLM 响应 — 所有消费者只接触这个类型"""
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None


# ---- Protocol ----


@runtime_checkable
class Provider(Protocol):
    """LLM 提供者协议"""

    @property
    def model(self) -> str: ...

    async def call(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Response: ...


# ---- OpenAI Implementation ----


class OpenAIProvider:
    """OpenAI 兼容 API 实现 — 唯一知道 SDK 细节的地方"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._extra_body = extra_body

    @property
    def model(self) -> str:
        return self._model

    async def call(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Response:
        openai_messages = [{"role": "system", "content": system}, *messages]

        raw = await self._client.chat.completions.create(
            model=self._model,
            messages=openai_messages,
            tools=tools or None,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            extra_body=self._extra_body,
        )

        choice = raw.choices[0]
        message = choice.message

        # Map tool calls
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

        # Map usage
        usage = None
        if raw.usage:
            usage = Usage(
                prompt_tokens=raw.usage.prompt_tokens or 0,
                completion_tokens=raw.usage.completion_tokens or 0,
            )

        return Response(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.finish_reason,
        )

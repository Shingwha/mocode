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

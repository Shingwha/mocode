"""Tool utility functions."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.provider import Provider


def truncate_result(
    result: str,
    limit: int,
    truncate_message: str = "\n...[truncated, use limit/offset parameters to read more]...",
) -> str:
    if limit <= 0 or len(result) <= limit:
        return result
    return result[:limit] + truncate_message


def resolve_provider_getter(
    provider: Provider | Callable[[], Provider],
) -> Callable[[], Provider]:
    """Normalize Provider or provider-getter into a () -> Provider callable."""
    if callable(provider) and not hasattr(provider, "call"):
        return provider
    _p = provider
    return lambda: _p


def build_tool_result(tool_call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

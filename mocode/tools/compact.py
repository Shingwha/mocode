"""Compact — context compression for long conversations.

CompactManager tracks token usage and generates LLM summaries to replace
old messages. CompactTool exposes this as an LLM-callable tool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..core.tool import Tool
from ..prompts.compact import summary_system_prompt, COMPACT_USER_TEMPLATE
from .utils import resolve_provider_getter

if TYPE_CHECKING:
    from ..core.agent import AgentLoop
    from ..core.hook import Hooks
    from ..core.provider import Provider

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 128_000


# ---- Message formatting helpers (pure functions) ----


def find_turn_starts(messages: list[dict]) -> list[int]:
    return [i for i, msg in enumerate(messages) if msg.get("role") == "user"]


def strip_tool_messages(messages: list[dict]) -> list[dict]:
    return [
        msg for msg in messages
        if msg.get("role") != "tool"
        and not (msg.get("role") == "assistant" and msg.get("tool_calls"))
    ]


def format_messages_for_summary(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "user":
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif part.get("type") == "image_url":
                            text_parts.append("[image attached]")
                        else:
                            text_parts.append("[attachment]")
                    else:
                        text_parts.append(str(part))
                content = " ".join(text_parts)
            parts.append(f"[User] {content}")

        elif role == "assistant":
            text = content or ""
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        name = fn.get("name", "unknown")
                        args = fn.get("arguments", "")
                        text += f"\n[Tool Call: {name}({args})]"
            parts.append(f"[Assistant] {text}")

        elif role == "tool":
            parts.append(f"[Tool] {content}")

    return "\n\n".join(parts)


def build_fallback_summary(messages: list[dict]) -> str:
    first_user = ""
    last_user = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
            if not first_user:
                first_user = content[:300]
            last_user = content[:300]
    return (
        f"[Conversation summary ({len(messages)} messages compressed)]\n"
        f"User's first message: {first_user}\n"
        f"Last discussed: {last_user}"
    )


# ---- CompactManager (orchestration only) ----


class CompactManager:
    """Context compression — tracks token usage and generates summaries."""

    def __init__(
        self,
        provider: Provider | Callable[[], Provider],
        hooks: Hooks | None = None,
        threshold: float = 0.80,
        keep_recent_turns: int = 0,
        context_windows: dict[str, int] | None = None,
    ):
        self._provider_getter = resolve_provider_getter(provider)
        self._hooks: Hooks | None = hooks
        self._threshold = threshold
        self._keep_recent_turns = keep_recent_turns
        self._context_windows = context_windows or {}
        self._last_prompt_tokens: int = 0

    @property
    def _provider(self) -> Provider:
        return self._provider_getter()

    @property
    def model(self) -> str:
        return self._provider.model

    def update_provider(self, provider: Provider | Callable[[], Provider]) -> None:
        self._provider_getter = resolve_provider_getter(provider)

    def update_usage(self, prompt_tokens: int) -> None:
        self._last_prompt_tokens = prompt_tokens

    def reset(self) -> None:
        self._last_prompt_tokens = 0

    @property
    def last_prompt_tokens(self) -> int:
        return self._last_prompt_tokens

    def get_context_window(self, model: str) -> int:
        return self._context_windows.get(model, DEFAULT_CONTEXT_WINDOW)

    def should_compact(self, model: str) -> bool:
        if self._last_prompt_tokens == 0:
            return False
        context_window = self.get_context_window(model)
        return self._last_prompt_tokens > context_window * self._threshold

    # ---- Hook-based registration ----

    async def pre_loop_hook(self, messages: list[dict]) -> list[dict]:
        """Pre-loop hook: compact if threshold exceeded."""
        if self.should_compact(self._provider.model):
            return await self.compact(messages, self._provider.model)
        return messages

    def register(self, agent_loop: AgentLoop) -> None:
        """Register hooks on an AgentLoop."""
        agent_loop.hooks.on("pre_loop", self.pre_loop_hook)
        if self._hooks:
            self._hooks.on("usage_update", self._on_usage_update)

    def _on_usage_update(self, data) -> None:
        self.update_usage(data["prompt_tokens"])

    # ---- Core compact logic ----

    async def _generate_summary(self, messages_text: str) -> str:
        try:
            resp = await self._provider.call(
                messages=[{
                    "role": "user",
                    "content": COMPACT_USER_TEMPLATE.format(messages_text=messages_text),
                }],
                system=summary_system_prompt.build(format="xml"),
                tools=[],
                max_tokens=8000,
            )
            return resp.content or ""
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return ""

    async def compact(self, messages: list[dict], model: str) -> list[dict]:
        turn_starts = find_turn_starts(messages)
        keep = self._keep_recent_turns

        if len(turn_starts) <= keep:
            return messages

        if keep == 0:
            recent_messages: list[dict] = []
        else:
            split_point = turn_starts[-keep]
            recent_messages = messages[split_point:]

        formatted = format_messages_for_summary(messages)
        summary = await self._generate_summary(formatted)
        if not summary:
            summary = build_fallback_summary(messages)

        recent_cleaned = strip_tool_messages(recent_messages)

        new_messages = [
            {
                "role": "user",
                "content": f"[Context Summary]\n{summary}\n[End of summary]",
            },
            {
                "role": "assistant",
                "content": "Understood, I will continue based on the summary.",
            },
            *recent_cleaned,
        ]

        if self._hooks:
            await self._hooks.emit("context_compact", {
                "old_count": len(messages),
                "new_count": len(new_messages),
                "compressed_count": len(messages) - len(new_messages),
            })

        self._last_prompt_tokens = 0
        logger.info(
            f"Compacted: {len(messages)} -> {len(new_messages)} messages "
            f"(compressed {len(messages) - len(new_messages)} messages)"
        )
        return new_messages


def CompactTool(
    get_messages: Callable[[], list[dict]],
    compact_manager: CompactManager,
    provider: Provider | Callable[[], Provider] | None = None,
) -> Tool:
    """Create a tool that lets the LLM trigger context compression.

    Args:
        get_messages: Returns the current message list (mutated in place).
        compact_manager: The CompactManager instance for tracking state.
        provider: Optional custom provider for summary generation.
                  If provided, overrides the compact_manager's provider.
    """
    if provider is not None:
        compact_manager.update_provider(resolve_provider_getter(provider))

    async def _compact(args: dict) -> str:
        messages = get_messages()
        if not messages:
            return "No messages to compact"
        new_messages = await compact_manager.compact(messages, compact_manager.model)
        messages.clear()
        messages.extend(new_messages)
        return f"Context compacted: {len(new_messages)} messages remaining"

    return Tool(
        "compact",
        "Compress conversation history to free up context window. Returns a summary of older messages.",
        {},
        _compact,
    )

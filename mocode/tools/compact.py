"""Compact — context compression for long conversations.

Provides:
- Pure functions for message formatting and compression
- compact_messages() — core compression logic
- CompactHook(AgentHook) — auto-trigger via before_iteration
- CompactTool — LLM-callable tool for manual trigger
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..core.hook import AgentHook, AgentHookContext
from ..core.tool import Tool
from ..prompts.compact import summary_system_prompt, COMPACT_USER_TEMPLATE

logger = logging.getLogger(__name__)


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


# ---- Core compression (pure async function) ----


async def _generate_summary(provider, messages_text: str) -> str:
    try:
        resp = await provider.call(
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


async def compact_messages(
    provider,
    messages: list[dict],
    *,
    keep_recent_turns: int = 0,
) -> list[dict]:
    """Compress messages by generating an LLM summary of older content."""
    turn_starts = find_turn_starts(messages)
    keep = keep_recent_turns

    if len(turn_starts) <= keep:
        return messages

    if keep == 0:
        recent_messages: list[dict] = []
    else:
        split_point = turn_starts[-keep]
        recent_messages = messages[split_point:]

    formatted = format_messages_for_summary(messages)
    summary = await _generate_summary(provider, formatted)
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

    logger.info(
        f"Compacted: {len(messages)} -> {len(new_messages)} messages "
        f"(compressed {len(messages) - len(new_messages)} messages)"
    )
    return new_messages


# ---- CompactHook (AgentHook) ----


class CompactHook(AgentHook):
    """Auto-trigger context compression when token usage exceeds threshold."""

    def __init__(
        self,
        provider,
        threshold: float = 0.80,
        keep_recent_turns: int = 0,
        context_window: int = 128_000,
    ):
        self._provider = provider
        self._threshold = threshold
        self._keep_recent_turns = keep_recent_turns
        self._context_window = context_window
        self._last_prompt_tokens: int = 0

    @property
    def last_prompt_tokens(self) -> int:
        return self._last_prompt_tokens

    async def before_iteration(self, ctx: AgentHookContext) -> None:
        if ctx.usage:
            self._last_prompt_tokens = ctx.usage.prompt_tokens
        if self._last_prompt_tokens > self._context_window * self._threshold:
            old_count = len(ctx.messages)
            ctx.messages[:] = await compact_messages(
                self._provider, ctx.messages,
                keep_recent_turns=self._keep_recent_turns,
            )
            ctx.compact_old = old_count
            ctx.compact_new = len(ctx.messages)
            self._last_prompt_tokens = 0
            await self.on_compact(ctx)


# ---- CompactTool (Tool factory) ----


def CompactTool(
    provider,
    get_messages: Callable[[], list[dict]],
    *,
    keep_recent_turns: int = 0,
) -> Tool:
    """Create a tool that lets the LLM trigger context compression."""
    async def _compact(args: dict) -> str:
        messages = get_messages()
        if not messages:
            return "No messages to compact"
        new_messages = await compact_messages(
            provider, messages,
            keep_recent_turns=keep_recent_turns,
        )
        messages.clear()
        messages.extend(new_messages)
        return f"Context compacted: {len(new_messages)} messages remaining"

    return Tool(
        "compact",
        "Compress conversation history to free up context window. Returns a summary of older messages.",
        {},
        _compact,
    )

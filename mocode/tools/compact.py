"""Compact — context compression for long conversations.

Provides:
- Pure functions for message formatting and compression
- compact_messages() — core compression logic
- CompactTool — LLM-callable tool for manual trigger

CompactHook lives in mocode/hooks/compact.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..core.tool import Tool
from ..prompts.compact import summary_system_prompt, COMPACT_USER_TEMPLATE

logger = logging.getLogger(__name__)


# ---- Message formatting helpers (pure functions) ----


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
) -> list[dict]:
    """Compress messages by generating an LLM summary."""
    if len(messages) <= 2:
        return messages

    formatted = format_messages_for_summary(messages)
    summary = await _generate_summary(provider, formatted)
    if not summary:
        summary = build_fallback_summary(messages)

    new_messages = [
        {
            "role": "user",
            "content": f"[Context Summary]\n{summary}\n[End of summary]",
        },
    ]

    logger.info(
        f"Compacted: {len(messages)} -> {len(new_messages)} messages "
        f"(compressed {len(messages) - len(new_messages)} messages)"
    )
    return new_messages


# ---- CompactTool (Tool factory) ----


def CompactTool(
    provider,
    get_messages: Callable[[], list[dict]],
) -> Tool:
    """Create a tool that lets the LLM trigger context compression."""
    async def _compact(args: dict) -> str:
        messages = get_messages()
        if not messages:
            return "No messages to compact"
        new_messages = await compact_messages(provider, messages)
        messages.clear()
        messages.extend(new_messages)
        return f"Context compacted: {len(new_messages)} messages remaining"

    return Tool(
        "compact",
        "Compress conversation history to free up context window. Returns a summary of older messages.",
        {},
        _compact,
    )

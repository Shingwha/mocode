"""Compact — context compression for long conversations.

Core compression logic, extracted from tools/compact.py to break
hooks → tools → prompts dependency chain.
"""

from __future__ import annotations


def _format_content_parts(content: list) -> str:
    """Flatten a list of content parts into a single string."""
    text_parts = []
    for part in content:
        if not isinstance(part, dict):
            text_parts.append(str(part))
            continue
        part_type = part.get("type", "")
        if part_type == "text":
            text_parts.append(part.get("text", ""))
        elif part_type == "image_url":
            text_parts.append("[image attached]")
        else:
            text_parts.append("[attachment]")
    return " ".join(text_parts)


def _format_tool_calls(tool_calls: list) -> str:
    """Format tool calls into a readable string."""
    parts = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function", {})
        name = fn.get("name", "unknown")
        args = fn.get("arguments", "")
        parts.append(f"[Tool Call: {name}({args})]")
    return "\n".join(parts)


def format_messages_for_summary(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "user":
            if isinstance(content, list):
                content = _format_content_parts(content)
            parts.append(f"[User] {content}")

        elif role == "assistant":
            text = content or ""
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                text += "\n" + _format_tool_calls(tool_calls)
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


async def _generate_summary(
    provider,
    messages_text: str,
    system_prompt: str,
    user_template: str,
) -> str:
    try:
        resp = await provider.call(
            messages=[
                {
                    "role": "user",
                    "content": user_template.format(messages_text=messages_text),
                }
            ],
            system=system_prompt,
            tools=[],
            max_tokens=8000,
        )
        return resp.content or ""
    except Exception:
        return ""


async def compact_messages(
    provider,
    messages: list[dict],
    system_prompt: str,
    user_template: str,
) -> list[dict]:
    """Compress messages by generating an LLM summary."""
    if len(messages) <= 2:
        return messages

    formatted = format_messages_for_summary(messages)
    summary = await _generate_summary(provider, formatted, system_prompt, user_template)
    if not summary:
        summary = build_fallback_summary(messages)

    new_messages = [
        {
            "role": "user",
            "content": f"[Context Summary]\n{summary}\n[End of summary]",
        },
    ]

    return new_messages

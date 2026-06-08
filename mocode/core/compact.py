"""Compact — context compression for long conversations.

Core compression logic, extracted from tools/compact.py to break
hooks → tools → prompts dependency chain.
"""

from __future__ import annotations

from collections import Counter

from .provider import with_retry

# ---- Constants for preprocessing ----

TOOL_RESULT_MAX_LEN = 800
TOOL_RESULT_HALF = TOOL_RESULT_MAX_LEN // 2
TOOL_ARGS_MAX_LEN = 200


# ---- Content helpers ----


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


def _extract_tool_name(tc: dict) -> str:
    """Extract tool name from a tool_calls entry."""
    return tc.get("function", {}).get("name", "unknown")


def _truncate_tool_calls(tool_calls: list) -> list:
    """Return a shallow copy with long arguments truncated."""
    out = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            out.append(tc)
            continue
        fn = tc.get("function", {})
        args = fn.get("arguments", "")
        if len(args) > TOOL_ARGS_MAX_LEN:
            fn = {
                **fn,
                "arguments": args[:TOOL_ARGS_MAX_LEN] + "... [truncated]",
            }
            tc = {**tc, "function": fn}
        out.append(tc)
    return out


def _truncate_tool_result(content: str) -> str:
    """Truncate a tool result, preserving head and tail."""
    if len(content) <= TOOL_RESULT_MAX_LEN:
        return content
    head = content[:TOOL_RESULT_HALF]
    tail = content[-TOOL_RESULT_HALF:]
    return f"{head}\n... [truncated to {TOOL_RESULT_MAX_LEN} chars]\n{tail}"


# ---- Preprocessing ----


def _preprocess_messages(messages: list[dict]) -> list[dict]:
    """Shrink messages before sending to the LLM for summarization.

    Truncate long tool results and long tool call arguments.
    """
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "tool":
            content = msg.get("content", "")
            if len(content) > TOOL_RESULT_MAX_LEN:
                msg = {**msg, "content": _truncate_tool_result(content)}
        elif role == "assistant":
            tc = msg.get("tool_calls")
            if tc:
                msg = {**msg, "tool_calls": _truncate_tool_calls(tc)}
        result.append(msg)
    return result


# ---- Formatting ----


def _format_tool_calls(tool_calls: list) -> str:
    """Format tool calls into a readable string."""
    parts = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function", {})
        name = _extract_tool_name(tc)
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


# ---- Fallback summary ----


def build_fallback_summary(messages: list[dict]) -> str:
    """Build a structured fallback summary when LLM is unavailable."""
    # Extract user messages
    user_msgs: list[str] = []
    last_assistant = ""
    tool_counter: Counter[str] = Counter()

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            text = content
            if isinstance(text, list):
                text = _format_content_parts(text)
            # Take first sentence or first 200 chars
            first_line = text.split("\n", 1)[0][:200]
            if first_line:
                user_msgs.append(first_line)

        elif role == "assistant":
            if content:
                last_assistant = content[:300]
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict):
                        tool_counter[_extract_tool_name(tc)] += 1

    parts = [f"[Conversation summary ({len(messages)} messages compressed)]"]

    # Intent: first user message
    if user_msgs:
        parts.append(f"\n[Intent]\n{user_msgs[0]}")

    # User messages overview
    if len(user_msgs) > 1:
        parts.append("\n[User Messages]")
        for i, m in enumerate(user_msgs, 1):
            parts.append(f"  {i}. {m}")

    # Last assistant state
    if last_assistant:
        parts.append(f"\n[Last State]\n{last_assistant}")

    # Tool usage stats
    if tool_counter:
        stats = ", ".join(f"{n}: {c}" for n, c in tool_counter.most_common(5))
        parts.append(f"\n[Tool Usage]\n{stats}")

    return "\n".join(parts)


# ---- LLM summary generation ----


async def _generate_summary(
    provider,
    messages_text: str,
    system_prompt: str,
    user_template: str,
) -> str:
    try:
        resp = await with_retry(
            provider.call,
            messages=[
                {
                    "role": "user",
                    "content": user_template.format(messages_text=messages_text),
                }
            ],
            system=system_prompt,
            tools=[],
            max_tokens=4000,
        )
        return resp.content or ""
    except Exception:
        return ""


# ---- Public API ----


async def compact_messages(
    provider,
    messages: list[dict],
    system_prompt: str,
    user_template: str,
) -> list[dict]:
    """Compress messages by generating an LLM summary."""
    if len(messages) <= 2:
        return messages

    preprocessed = _preprocess_messages(messages)
    formatted = format_messages_for_summary(preprocessed)
    summary = await _generate_summary(provider, formatted, system_prompt, user_template)
    if not summary:
        summary = build_fallback_summary(preprocessed)

    new_messages = [
        {
            "role": "user",
            "content": f"[Context Summary]\n{summary}\n[End of summary]",
        },
    ]

    return new_messages

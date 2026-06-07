"""Session Markdown export — render Session objects as human-readable Markdown.

Usage:
    from mocode.app.export import render_session_md

    md = render_session_md(session, system_prompt="You are a helpful assistant.")
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .session import Session, _extract_title


def _text_content(content: Any) -> str:
    """Extract plain text from message content (handles str and list-of-parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    parts.append("[image attached]")
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts)
    return str(content) if content else ""


def _short_args(arguments: str, max_len: int = 60) -> str:
    """Return a short display string for tool call arguments."""
    try:
        args = json.loads(arguments)
        if isinstance(args, dict):
            # Show first key-value pair as hint
            items = list(args.items())
            if items:
                k, v = items[0]
                v_str = str(v)
                if len(v_str) > 30:
                    v_str = v_str[:27] + "..."
                return f"{k}={v_str}"
        return arguments[:max_len]
    except (json.JSONDecodeError, TypeError):
        return arguments[:max_len]


def _render_frontmatter(session: Session) -> list[str]:
    """Render YAML frontmatter for session export."""
    lines: list[str] = []
    lines.append("---")
    lines.append(f"session_id: {session.id}")
    lines.append(f"exported_at: {datetime.now().isoformat()}")
    lines.append(f"workdir: {session.workdir}")
    lines.append(f"message_count: {len(session.messages)}")
    if session.model:
        lines.append(f"model: {session.model}")
    if session.provider:
        lines.append(f"provider: {session.provider}")
    lines.append("---")
    lines.append("")
    return lines


def _render_overview(session: Session) -> list[str]:
    """Render overview section with title and statistics."""
    lines: list[str] = []
    title = session.title or _extract_title(session.messages) or "Session"
    lines.append(f"# MoCode Session Export")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Topic**: {title}")

    # Count turns and tool calls
    turn_count = 0
    tool_call_count = 0
    for msg in session.messages:
        if msg.get("role") == "user":
            turn_count += 1
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tool_call_count += len(msg["tool_calls"])
    lines.append(f"- **Conversation**: {turn_count} turns | {tool_call_count} tool calls")
    lines.append("")
    return lines


def _render_tool_call(tc: dict[str, Any]) -> list[str]:
    """Render a single tool call as Markdown."""
    lines: list[str] = []
    func = tc.get("function", {})
    tc_name = func.get("name", "unknown")
    tc_args = func.get("arguments", "{}")
    tc_id = tc.get("id", "")

    lines.append(f"#### Tool Call: {tc_name} (`{_short_args(tc_args)}`)")
    if tc_id:
        lines.append(f"<!-- call_id: {tc_id} -->")
    lines.append("```json")
    # Pretty-print arguments if valid JSON
    try:
        parsed = json.loads(tc_args)
        lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
    except (json.JSONDecodeError, TypeError):
        lines.append(tc_args)
    lines.append("```")
    lines.append("")
    return lines


def _render_tool_result(tc_id: str, content: str, msgs: list[dict[str, Any]]) -> list[str]:
    """Render a tool result as collapsible Markdown."""
    lines: list[str] = []
    # Try to find the matching tool call name
    tc_name = "Tool"
    for prev_msg in msgs:
        for prev_tc in prev_msg.get("tool_calls", []):
            if prev_tc.get("id") == tc_id:
                tc_name = prev_tc.get("function", {}).get("name", "Tool")
                break

    lines.append(f"<details><summary>Tool Result: {tc_name}</summary>")
    lines.append("")
    if tc_id:
        lines.append(f"<!-- call_id: {tc_id} -->")
    lines.append(content)
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines


def _render_assistant_message(msg: dict[str, Any], msgs: list[dict[str, Any]]) -> list[str]:
    """Render an assistant message with reasoning, content, and tool calls."""
    lines: list[str] = []
    reasoning = msg.get("reasoning_content", "")
    content = _text_content(msg.get("content", ""))
    tool_calls = msg.get("tool_calls", [])

    lines.append("### Assistant")
    lines.append("")

    # Reasoning (thinking) in collapsible
    if reasoning:
        lines.append("<details><summary>Thinking</summary>")
        lines.append("")
        lines.append(reasoning)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Text content
    if content:
        lines.append(content)
        lines.append("")

    # Tool calls
    for tc in tool_calls:
        lines.extend(_render_tool_call(tc))

    return lines


def _render_turn(turn_msgs: list[dict[str, Any]], turn_num: int, all_msgs: list[dict[str, Any]]) -> list[str]:
    """Render a complete turn (user message + subsequent assistant/tool messages)."""
    lines: list[str] = []
    lines.append("---")
    lines.append("")
    lines.append(f"## Turn {turn_num}")
    lines.append("")

    # First message should be user
    if turn_msgs and turn_msgs[0].get("role") == "user":
        lines.append("### User")
        lines.append("")
        lines.append(_text_content(turn_msgs[0].get("content", "")))
        lines.append("")
        turn_msgs = turn_msgs[1:]

    # Process remaining messages in this turn
    for msg in turn_msgs:
        role = msg.get("role")
        if role == "assistant":
            lines.extend(_render_assistant_message(msg, all_msgs))
        elif role == "tool":
            tc_id = msg.get("tool_call_id", "")
            result_content = _text_content(msg.get("content", ""))
            lines.extend(_render_tool_result(tc_id, result_content, all_msgs))
        # Unknown roles are skipped

    return lines


def _walk_messages(msgs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group messages into turns. Each turn starts with a user message."""
    turns: list[list[dict[str, Any]]] = []
    current_turn: list[dict[str, Any]] = []

    for msg in msgs:
        if msg.get("role") == "user":
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        else:
            if not current_turn:
                # Orphan non-user message at top level — create a turn for it
                current_turn = []
            current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return turns


def render_session_md(session: Session, system_prompt: str = "") -> str:
    """Render a Session as a Markdown string."""
    lines: list[str] = []

    # --- YAML frontmatter ---
    lines.extend(_render_frontmatter(session))

    # --- Overview ---
    lines.extend(_render_overview(session))

    # --- System Prompt ---
    if system_prompt:
        lines.append("---")
        lines.append("")
        lines.append("## System Prompt")
        lines.append("")
        lines.append(system_prompt)
        lines.append("")

    # --- Turns ---
    turns = _walk_messages(session.messages)
    for turn_num, turn_msgs in enumerate(turns, 1):
        lines.extend(_render_turn(turn_msgs, turn_num, session.messages))

    return "\n".join(lines)

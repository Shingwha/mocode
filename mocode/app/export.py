"""Session Markdown export — render a Session as human-readable Markdown."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .session import Session, extract_title


def render_session_md(session: Session, system_prompt: str = "") -> str:
    """Render a Session as a Markdown string."""
    lines = _frontmatter(session) + _overview(session)
    if system_prompt:
        lines += _block("## System Prompt", system_prompt)
    for number, turn in enumerate(_turns(session.messages), 1):
        lines += _turn(turn, number, session.messages)
    return "\n".join(lines)


# ── Sections ────────────────────────────────────────────────


def _frontmatter(session: Session) -> list[str]:
    lines = [
        "---",
        f"session_id: {session.id}",
        f"exported_at: {datetime.now().isoformat()}",
        f"workdir: {session.workdir}",
        f"message_count: {len(session.messages)}",
    ]
    if session.model:
        lines.append(f"model: {session.model}")
    if session.provider:
        lines.append(f"provider: {session.provider}")
    return lines + ["---", ""]


def _overview(session: Session) -> list[str]:
    title = session.title or extract_title(session.messages) or "Session"
    turns = sum(1 for m in session.messages if m.get("role") == "user")
    calls = sum(
        len(m["tool_calls"])
        for m in session.messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    return [
        "# MoCode Session Export",
        "",
        "## Overview",
        "",
        f"- **Topic**: {title}",
        f"- **Conversation**: {turns} turns | {calls} tool calls",
        "",
    ]


def _block(heading: str, body: str) -> list[str]:
    return ["---", "", heading, "", body, ""]


def _turn(turn: list[dict[str, Any]], number: int, all_messages: list[dict[str, Any]]) -> list[str]:
    lines = ["---", "", f"## Turn {number}", ""]
    rest = turn
    if turn and turn[0].get("role") == "user":
        lines += ["### User", "", _text(turn[0].get("content", "")), ""]
        rest = turn[1:]

    for msg in rest:
        role = msg.get("role")
        if role == "assistant":
            lines += _assistant(msg)
        elif role == "tool":
            lines += _tool_result(
                msg.get("tool_call_id", ""), _text(msg.get("content", "")), all_messages
            )
    return lines


def _assistant(msg: dict[str, Any]) -> list[str]:
    lines = ["### Assistant", ""]
    reasoning = msg.get("reasoning_content", "")
    if reasoning:
        lines += ["<details><summary>Thinking</summary>", "", reasoning, "", "</details>", ""]
    content = _text(msg.get("content", ""))
    if content:
        lines += [content, ""]
    for call in msg.get("tool_calls", []):
        lines += _tool_call(call)
    return lines


def _tool_call(call: dict[str, Any]) -> list[str]:
    function = call.get("function", {})
    name = function.get("name", "unknown")
    arguments = function.get("arguments", "{}")
    lines = [f"#### Tool Call: {name} (`{_short_args(arguments)}`)"]
    if call.get("id"):
        lines.append(f"<!-- call_id: {call['id']} -->")
    lines.append("```json")
    try:
        lines.append(json.dumps(json.loads(arguments), ensure_ascii=False, indent=2))
    except (json.JSONDecodeError, TypeError):
        lines.append(arguments)
    return lines + ["```", ""]


def _tool_result(call_id: str, content: str, messages: list[dict[str, Any]]) -> list[str]:
    name = "Tool"
    for msg in messages:
        for call in msg.get("tool_calls", []):
            if call.get("id") == call_id:
                name = call.get("function", {}).get("name", "Tool")
    lines = [f"<details><summary>Tool Result: {name}</summary>", ""]
    if call_id:
        lines.append(f"<!-- call_id: {call_id} -->")
    return lines + [content, "", "</details>", ""]


def _turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group messages into turns; each turn starts at a user message."""
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "user" and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


def _text(content: Any) -> str:
    """Plain text from message content (string or list of parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    parts.append("[image attached]")
        return "\n".join(parts)
    return str(content) if content else ""


def _short_args(arguments: str, max_len: int = 60) -> str:
    """One-line hint for a tool call's arguments."""
    try:
        args = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return arguments[:max_len]
    if isinstance(args, dict) and args:
        key, value = next(iter(args.items()))
        text = str(value)
        if len(text) > 30:
            text = text[:27] + "..."
        return f"{key}={text}"
    return arguments[:max_len]

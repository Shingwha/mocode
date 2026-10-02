"""Session export — render a Session as Markdown or portable JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.transcript import (
    answered_call_id,
    is_assistant,
    is_tool_result,
    is_user,
    reasoning_of,
    text_of,
    tool_call_by_id,
    tool_call_id,
    tool_call_name,
    tool_call_arguments,
    tool_calls_of,
)
from .io import write_json
from .session import Session, extract_title, timestamp


def export_session(path: Path, session: Session, system_prompt: str = "") -> None:
    """Write a session as a portable JSON file (re-importable anywhere)."""
    write_json(path, {"system_prompt": system_prompt, **session.to_dict()})


def export_session_md(session: Session, path: Path, system_prompt: str = "") -> None:
    """Write a session as a human-readable Markdown file."""
    md = render_session_md(session, system_prompt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")


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
        f"exported_at: {timestamp()}",
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
    turns = sum(1 for m in session.messages if is_user(m))
    calls = sum(
        len(tool_calls_of(m))
        for m in session.messages
        if is_assistant(m) and tool_calls_of(m)
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
    if turn and is_user(turn[0]):
        lines += ["### User", "", text_of(turn[0], sep="\n"), ""]
        rest = turn[1:]

    for msg in rest:
        if is_assistant(msg):
            lines += _assistant(msg)
        elif is_tool_result(msg):
            lines += _tool_result(
                answered_call_id(msg), text_of(msg, sep="\n"), all_messages
            )
    return lines


def _assistant(msg: dict[str, Any]) -> list[str]:
    lines = ["### Assistant", ""]
    reasoning = reasoning_of(msg)
    if reasoning:
        lines += ["<details><summary>Thinking</summary>", "", reasoning, "", "</details>", ""]
    content = text_of(msg, sep="\n")
    if content:
        lines += [content, ""]
    for call in tool_calls_of(msg):
        lines += _tool_call(call)
    return lines


def _tool_call(call: dict[str, Any]) -> list[str]:
    name = tool_call_name(call, "unknown")
    arguments = tool_call_arguments(call, "{}")
    call_id = tool_call_id(call)
    lines = [f"#### Tool Call: {name} (`{_short_args(arguments)}`)"]
    if call_id:
        lines.append(f"<!-- call_id: {call_id} -->")
    lines.append("```json")
    try:
        lines.append(json.dumps(json.loads(arguments), ensure_ascii=False, indent=2))
    except (json.JSONDecodeError, TypeError):
        lines.append(arguments)
    return lines + ["```", ""]


def _tool_result(call_id: str, content: str, messages: list[dict[str, Any]]) -> list[str]:
    call = tool_call_by_id(messages, call_id)
    name = tool_call_name(call, "Tool") if call is not None else "Tool"
    lines = [f"<details><summary>Tool Result: {name}</summary>", ""]
    if call_id:
        lines.append(f"<!-- call_id: {call_id} -->")
    return lines + [content, "", "</details>", ""]


def _turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group messages into turns; each turn starts at a user message."""
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for msg in messages:
        if is_user(msg) and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


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

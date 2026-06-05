"""Display helpers — tool grouping, batching, and summary formatting."""

from __future__ import annotations

import json

# ── Tool display helpers ────────────────────────────────

_TOOL_KEY = {
    "read": "path",
    "write": "path",
    "append": "path",
    "edit": "path",
    "bash": "command",
    "glob": "pattern",
    "grep": "pattern",
    "fetch": "url",
    "sub_agent": "task",
    "skill": "name",
    "goal": "action",
    "image": "prompt",
}


def tool_summary(name: str, args: dict) -> str:
    """Extract a short summary string from tool arguments."""
    key = _TOOL_KEY.get(name)
    if not key:
        return ""
    val = str(args.get(key, ""))
    return val[:60] + ("..." if len(val) > 60 else "")


# ── Tool call batching ──────────────────────────────────

_MERGE_TOOLS = frozenset({"read", "write", "append", "edit", "glob", "grep"})
_MERGE_LIMIT = 100


def group_items(
    items: list,
    get_name,
    get_args,
) -> list[tuple[str, list[str]]]:
    """Group items by tool name. Mergeable tools are grouped; others stay individual."""
    merged: dict[str, list[str]] = {}
    singles: list[tuple[str, list[str]]] = []
    for item in items:
        name = get_name(item)
        args = get_args(item)
        summary = tool_summary(name, args)
        if name in _MERGE_TOOLS:
            merged.setdefault(name, []).append(summary)
        else:
            singles.append((name, [summary]))
    return list(merged.items()) + singles


def group_tool_calls(tool_calls) -> list[tuple[str, list[str]]]:
    """Group OpenAI-style tool_calls by name."""
    return group_items(
        tool_calls,
        get_name=lambda tc: tc.name,
        get_args=lambda tc: json.loads(tc.arguments) if isinstance(tc.arguments, str) else (tc.arguments or {}),
    )


def merge_summaries(summaries: list[str]) -> str:
    """Join summaries with ', ', truncate at _MERGE_LIMIT with '… +N' suffix."""
    if not summaries:
        return ""
    joined = ", ".join(summaries)
    if len(joined) <= _MERGE_LIMIT:
        return joined
    # Fit as many as possible, reserve space for suffix
    total = 0
    count = 0
    for s in summaries:
        add = len(s) + (2 if count > 0 else 0)
        if total + add > _MERGE_LIMIT - 10:
            break
        total += add
        count += 1
    if count == 0:
        count = 1
    shown = ", ".join(summaries[:count])
    remaining = len(summaries) - count
    return shown + f"… +{remaining}" if remaining else shown

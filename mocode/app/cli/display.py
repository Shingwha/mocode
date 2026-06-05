"""CLI display — output rendering, delegates input to Input and spinner to SpinnerRunner."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .spinner import Priority, SpinnerRunner, Truncate
from .theme import (
    BG_USER,
    DIM,
    GREEN,
    RED,
    Theme,
    _s,
)

if TYPE_CHECKING:
    from .input import Input


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


def _tool_summary(name, args):
    key = _TOOL_KEY.get(name)
    if not key:
        return ""
    val = str(args.get(key, ""))
    return val[:60] + ("..." if len(val) > 60 else "")


# ── Tool call batching helpers ─────────────────────────


_MERGE_TOOLS = frozenset({"read", "write", "append", "edit", "glob", "grep"})
_MERGE_LIMIT = 100


def _group_items(
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
        summary = _tool_summary(name, args)
        if name in _MERGE_TOOLS:
            merged.setdefault(name, []).append(summary)
        else:
            singles.append((name, [summary]))
    return list(merged.items()) + singles


def _group_tool_calls(tool_calls) -> list[tuple[str, list[str]]]:
    return _group_items(
        tool_calls,
        get_name=lambda tc: tc.name,
        get_args=lambda tc: json.loads(tc.arguments) if isinstance(tc.arguments, str) else (tc.arguments or {}),
    )


def _merge_summaries(summaries: list[str]) -> str:
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


# ── Display ─────────────────────────────────────────────


class Display:
    """CLI display — output rendering. Input and spinner are delegated."""

    def __init__(self, input_: Input, theme: Theme | None = None):
        self.theme = theme or Theme()
        self._input = input_
        self._spinner = SpinnerRunner()
        self._pending_input: str | None = None

    # ── Input delegation ──────────────────────────────────

    def set_pending_input(self, text: str) -> None:
        """Queue text to pre-fill the next prompt."""
        self._pending_input = text

    async def prompt(self) -> str:
        default = self._pending_input or ""
        self._pending_input = None
        return await self._input.prompt(default=default)

    # ── Spinner delegation ────────────────────────────────

    def spinner_set(self, id: str, text: str = "",
                    priority: int | Priority = Priority.NORMAL,
                    truncate: Truncate | str = Truncate.TAIL) -> None:
        """Add or update a spinner segment."""
        self._spinner.set(id, text, priority, truncate)

    def spinner_remove(self, id: str) -> None:
        """Remove a spinner segment."""
        self._spinner.remove(id)

    def spinner(self, style=None):
        return self._spinner.spin(style)

    # ── Output core ───────────────────────────────────────

    def _print(self, *args, **kwargs):
        if self._spinner.active:
            self._spinner._clear_line()
        print(*args, **kwargs)

    def _styled(self, icon: str, text: str, color: str, icon_color: str = ""):
        """Format a line with icon + text + color."""
        ic = icon_color or color
        self._print(f"{_s(icon, ic)} {_s(text, color)}")

    # ── Output: user message ──────────────────────────────

    def user_message(self, content: str):
        """Render a user message with dark background."""
        t = self.theme
        self._print(_s(f"{t.icon_input} {content.strip()}", BG_USER, *t.color_user_fg))
        self._print()

    # ── Output: tool lifecycle ────────────────────────────

    def tool_done(self, name: str, merged: str, elapsed: float):
        """Print a successful tool line — ✓ with name, args, and optional elapsed."""
        t = self.theme
        elapsed_str = f" {_s(f'{elapsed:.1f}s', DIM)}" if elapsed >= 0.1 else ""
        self._print(
            f"{_s('✓', GREEN)} {_s(name, t.color_tool)}"
            f"{_s(f'({merged})', DIM)}{elapsed_str}"
        )

    def tool_fail(self, name: str, merged: str, error: str):
        """Print a failed tool line — ✗ with name, args, and error."""
        t = self.theme
        self._print(
            f"{_s('✗', RED)} {_s(name, t.color_tool)}"
            f"{_s(f'({merged}): ', DIM)}{_s(error, RED)}"
        )

    # ── Output: model response ────────────────────────────

    def reasoning(self, content: str):
        t = self.theme
        for line in content.splitlines():
            self._styled(t.icon_reasoning, line, t.color_reasoning)

    def text_response(self, content: str):
        t = self.theme
        for line in content.strip().splitlines():
            self._print(f"{_s(f'{t.icon_text} {line}', *t.color_text)}")

    def response(self, text: str):
        self._print(f"\n{text}\n")

    # ── Output: status ────────────────────────────────────

    def usage(self, prompt_tokens: int, completion_tokens: int):
        self._styled(
            self.theme.icon_usage,
            f"↑{prompt_tokens:,} ↓{completion_tokens:,}",
            self.theme.color_usage,
        )

    def compact(self, old: int, new: int):
        self._styled(
            self.theme.icon_compact,
            f"Compacted: {old} → {new} msgs",
            self.theme.color_compact,
        )

    def info(self, text: str):
        self._styled("", text, self.theme.color_info)

    def warn(self, text: str):
        self._styled("", text, self.theme.color_warn)

    def error(self, text: str):
        self._styled("", text, self.theme.color_error)

    # ── Screen ────────────────────────────────────────────

    def clear_screen(self):
        import os

        os.system("cls" if os.name == "nt" else "clear")

    # ── Resume rendering ──────────────────────────────────

    def render_messages(self, messages: list[dict]):
        """Re-render a message history as if it were live output."""
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "[image]") for p in content if isinstance(p, dict)
                    )
                self.user_message(content)
                i += 1
            elif role == "assistant":
                if msg.get("reasoning_content"):
                    self.reasoning(msg["reasoning_content"])
                if msg.get("content") and not msg.get("tool_calls"):
                    self.response(msg["content"])
                elif msg.get("content") and msg.get("tool_calls"):
                    self.text_response(msg["content"])
                tcs = msg.get("tool_calls", [])
                if tcs:
                    # Collect errors from tool result messages
                    errors: dict[str, str] = {}
                    j = i + 1
                    while j < len(messages) and messages[j].get("role") == "tool":
                        content = messages[j].get("content", "")
                        if content.startswith("error:") or content.startswith("timeout:"):
                            tcid = messages[j].get("tool_call_id", "")
                            for tc in tcs:
                                if tc.get("id") == tcid:
                                    errors[tc.get("function", {}).get("name", "?")] = content[:80]
                                    break
                        j += 1

                    # Group and display (matches live output format)
                    groups = _group_items(
                        tcs,
                        get_name=lambda tc: tc.get("function", {}).get("name", "?"),
                        get_args=lambda tc: (
                            json.loads(tc["function"]["arguments"])
                            if isinstance(tc.get("function", {}).get("arguments"), str)
                            else tc.get("function", {}).get("arguments") or {}
                        ),
                    )
                    for name, summaries in groups:
                        merged = _merge_summaries(summaries)
                        if name in errors:
                            self.tool_fail(name, merged, errors[name])
                        else:
                            self.tool_done(name, merged, 0)
                    i = j
                else:
                    i += 1
            else:
                i += 1

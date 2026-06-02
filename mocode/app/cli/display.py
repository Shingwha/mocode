"""CLI display — output rendering, delegates input to Input and spinner to SpinnerRunner."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .theme import RST, DIM, MAGENTA, BG_USER, Theme, _s
from .input import Input
from .spinner import SpinnerRunner

if TYPE_CHECKING:
    from .commands import Command


# ── Tool display helpers ────────────────────────────────


_TOOL_KEY = {
    "read": "path", "write": "path", "append": "path", "edit": "path",
    "bash": "command", "glob": "pattern", "grep": "pattern",
    "fetch": "url", "sub_agent": "task", "skill": "name",
    "goal": "action", "image": "prompt",
}


def _tool_summary(name, args):
    key = _TOOL_KEY.get(name)
    if not key:
        return ""
    val = str(args.get(key, ""))
    return val[:60] + ("..." if len(val) > 60 else "")


def _parse_tool_call(tc: dict) -> tuple[str, dict]:
    """Extract (name, args) from a raw tool_call message dict."""
    fn = tc.get("function", {})
    args = fn.get("arguments", "{}")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    return fn.get("name", "?"), args


# ── Tool call batching helpers ─────────────────────────


_MERGE_TOOLS = frozenset({"read", "write", "append", "edit", "glob", "grep"})
_MERGE_LIMIT = 100


def _group_tool_calls(tool_calls) -> list[tuple[str, list[str]]]:
    """Group ToolCall objects. Mergeable tools are grouped; others stay individual."""
    merged: dict[str, list[str]] = {}
    singles: list[tuple[str, list[str]]] = []
    for tc in tool_calls:
        args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else (tc.arguments or {})
        summary = _tool_summary(tc.name, args)
        if tc.name in _MERGE_TOOLS:
            merged.setdefault(tc.name, []).append(summary)
        else:
            singles.append((tc.name, [summary]))
    return list(merged.items()) + singles


def _group_tool_call_dicts(tcs: list[dict]) -> list[tuple[str, list[str]]]:
    """Group raw tool_call dicts. Mergeable tools are grouped; others stay individual."""
    merged: dict[str, list[str]] = {}
    singles: list[tuple[str, list[str]]] = []
    for tc in tcs:
        name, args = _parse_tool_call(tc)
        summary = _tool_summary(name, args)
        if name in _MERGE_TOOLS:
            merged.setdefault(name, []).append(summary)
        else:
            singles.append((name, [summary]))
    return list(merged.items()) + singles


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

    def __init__(self, theme: Theme | None = None):
        self.theme = theme or Theme()
        self._input = Input(ps1=self.theme.icon_input)
        self._spinner = SpinnerRunner()

    def set_commands(self, commands: list[Command]):
        """Set commands for autocomplete."""
        self._input.set_commands(commands)

    # ── Input delegation ──────────────────────────────────

    async def prompt(self) -> str:
        return await self._input.prompt()

    # ── Spinner delegation ────────────────────────────────

    def set_spinner_text(self, text: str):
        self._spinner.set_text(text)

    def set_spinner_detail(self, detail: str):
        self._spinner.set_detail(detail)

    def spinner(self, text: str = "Thinking", style=None):
        return self._spinner.spin(text, style)

    # ── Output core ───────────────────────────────────────

    def _print(self, *args, **kwargs):
        if self._spinner.active:
            self._spinner._clear()
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

    def tool_start(self, name: str, summary: str):
        t = self.theme
        self._print(f"{_s(t.icon_tool, DIM)} {_s(name, t.color_tool)}{_s(f'({summary})', DIM)}")

    def tool_start_batched(self, groups: list[tuple[str, list[str]]]):
        """Print merged tool call lines — one per tool type."""
        t = self.theme
        for name, summaries in groups:
            merged = _merge_summaries(summaries)
            self._print(f"{_s(t.icon_tool, DIM)} {_s(name, t.color_tool)}{_s(f'({merged})', DIM)}")

    def tool_error(self, msg: str):
        self._styled(self.theme.icon_error, msg, self.theme.color_error)

    def tool_timeout(self, seconds: int):
        self._styled(self.theme.icon_error, f"timeout: {seconds}s", self.theme.color_error)

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
        self._styled(self.theme.icon_usage, f"↑{prompt_tokens:,} ↓{completion_tokens:,}", self.theme.color_usage)

    def compact(self, old: int, new: int):
        self._styled(self.theme.icon_compact, f"Compacted: {old} → {new} msgs", self.theme.color_compact)

    def info(self, text: str):
        self._styled("", text, self.theme.color_info)

    def warn(self, text: str):
        self._styled("", text, self.theme.color_warn)

    def error(self, text: str):
        self._styled("", text, self.theme.color_error)

    # ── Screen ────────────────────────────────────────────

    def clear_screen(self):
        import os
        os.system('cls' if os.name == 'nt' else 'clear')

    # ── Resume rendering ──────────────────────────────────

    def render_messages(self, messages: list[dict]):
        """Re-render a message history as if it were live output."""
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "[image]") for p in content if isinstance(p, dict)
                    )
                self.user_message(content)
            elif role == "assistant":
                if msg.get("reasoning_content"):
                    self.reasoning(msg["reasoning_content"])
                if msg.get("content") and not msg.get("tool_calls"):
                    self.response(msg["content"])
                elif msg.get("content") and msg.get("tool_calls"):
                    self.text_response(msg["content"])
                tcs = msg.get("tool_calls", [])
                if tcs:
                    groups = _group_tool_call_dicts(tcs)
                    self.tool_start_batched(groups)
            elif role == "tool":
                content = msg.get("content", "")
                if content.startswith("error:") or content.startswith("timeout:"):
                    self.tool_error(content[:80])



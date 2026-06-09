"""CLI display — output rendering, tool grouping, and message re-rendering."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..utils import _CONTENT_LIMIT, group_items
from .palette import DEFAULT_PALETTE, ColorPalette
from .spinner import Priority, SpinnerRunner, Truncate
from .styles import DisplayStyles, SpinnerStyles
from .style import Style

if TYPE_CHECKING:
    from .input import Input


# ── Tool call batching ──────────────────────────────────────


def merge_summaries(summaries: list[str]) -> str:
    """Join summaries with ', ', truncate at _CONTENT_LIMIT with '… +N' suffix."""
    if not summaries:
        return ""
    joined = ", ".join(summaries)
    if len(joined) <= _CONTENT_LIMIT:
        return joined
    # Fit as many as possible, reserve space for suffix
    total = 0
    count = 0
    for s in summaries:
        add = len(s) + (2 if count > 0 else 0)
        if total + add > _CONTENT_LIMIT - 10:
            break
        total += add
        count += 1
    if count == 0:
        count = 1
    shown = ", ".join(summaries[:count])
    remaining = len(summaries) - count
    return shown + f"… +{remaining}" if remaining else shown


# ── Display ─────────────────────────────────────────────────


class Display:
    """CLI display — output rendering. Input and spinner are delegated.

    Core rendering pipeline::

        Display.print()        — raw output with spinner line-clearing
        Display.render_line()  — styled output using Style instances
    """

    def __init__(self, input_: Input,
                 styles: DisplayStyles | None = None,
                 palette: ColorPalette | None = None,
                 spinner_styles: SpinnerStyles | None = None,
                 spinner_palette: ColorPalette | None = None):
        self._s = styles or DisplayStyles()
        self._p = palette or DEFAULT_PALETTE
        self._input = input_
        self._spinner = SpinnerRunner(
            styles=spinner_styles,
            palette=spinner_palette,
        )
        self._pending_input: str | None = None

    # ── Input delegation ──────────────────────────────────

    def set_pending_input(self, text: str) -> None:
        """Queue text to pre-fill the next prompt."""
        self._pending_input = text

    def clear_session(self) -> None:
        """Clear paste store when starting a new conversation."""
        self._input.clear_session()

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

    def print(self, *args, **kwargs) -> None:
        """Raw print with spinner line-clearing. Public API."""
        if self._spinner.active:
            self._spinner._clear_line()
        print(*args, **kwargs)

    def render_line(self, style: Style, text: str, *,
                    suffix: str = "", elapsed: float = -1,
                    error: str = "") -> None:
        """Render a single styled line — the core output primitive.

        Args:
            style: Visual style (icon, colors)
            text: Main text content
            suffix: Optional dimmed context (e.g. tool args)
            elapsed: Optional elapsed seconds (shown if >= 0.1)
            error: Optional error text (rendered in icon color)
        """
        self.print(style.render(text, self._p,
                                suffix=suffix, elapsed=elapsed,
                                error=error))

    def _render_multiline(self, style: Style, content: str) -> None:
        """Render multi-line content with a style, one styled line per line."""
        for line in content.splitlines():
            self.render_line(style, line)

    def divider(self, style: str = "warning", width: int = 48) -> None:
        """Print a horizontal divider line."""
        self.print(self._p.s("─" * width, style))

    # ── Output: user message ──────────────────────────────

    def user_message(self, content: str) -> None:
        """Render a user message with dark background."""
        s = self._s.user_input
        self.print(self._p.s(f"{s.icon} {content.strip()}", s.bg, s.fg))
        self.print()

    # ── Output: tool lifecycle ────────────────────────────

    def tool_done(self, name: str, merged: str, elapsed: float) -> None:
        """Print a successful tool line — ✓ with name, args, and optional elapsed."""
        self.render_line(self._s.tool_done, name,
                         suffix=f"({merged})", elapsed=elapsed)

    def tool_fail(self, name: str, merged: str, error: str,
                  elapsed: float = -1) -> None:
        """Print a failed tool line — ✗ with name, args, error, and optional elapsed."""
        self.render_line(self._s.tool_fail, name,
                         suffix=f"({merged})" if merged else "",
                         error=error, elapsed=elapsed)

    # ── Output: model response ────────────────────────────

    def reasoning(self, content: str) -> None:
        """Render reasoning content (thinking)."""
        self._render_multiline(self._s.reasoning, content)

    def text_response(self, content: str) -> None:
        """Render text response with line-by-line styling."""
        self._render_multiline(self._s.text, content.strip())

    def response(self, text: str) -> None:
        """Render final response (raw text)."""
        self.print(f"\n{text}\n")

    # ── Output: status ────────────────────────────────────

    def usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.render_line(self._s.usage, f"↑{prompt_tokens:,} ↓{completion_tokens:,}")

    def compact(self, old: int, new: int) -> None:
        self.render_line(self._s.compact, f"Compacted: {old} → {new} msgs")

    def info(self, text: str) -> None:
        self.render_line(self._s.info, text)

    def warn(self, text: str) -> None:
        self.render_line(self._s.warn, text)

    def error(self, text: str) -> None:
        self.render_line(self._s.error, text)

    # ── Screen ────────────────────────────────────────────

    def clear_screen(self) -> None:
        import os
        os.system("cls" if os.name == "nt" else "clear")

    # ── Resume rendering ──────────────────────────────────

    def render_messages(self, messages: list[dict]) -> None:
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
                    j = i + 1
                    tool_msgs = []
                    while j < len(messages) and messages[j].get("role") == "tool":
                        tool_msgs.append(messages[j])
                        j += 1
                    self._render_tool_calls(tcs, tool_msgs)
                    i = j
                else:
                    i += 1
            else:
                i += 1

    def _render_tool_calls(self, tcs: list[dict], tool_messages: list[dict]) -> None:
        """Re-render tool calls from history, showing ✓/✗ with grouped summaries."""
        # Collect errors from tool result messages
        errors: dict[str, str] = {}
        for msg in tool_messages:
            content = msg.get("content", "")
            if content.startswith("error:") or content.startswith("timeout:"):
                tcid = msg.get("tool_call_id", "")
                for tc in tcs:
                    if tc.get("id") == tcid:
                        errors[tc.get("function", {}).get("name", "?")] = content[:80]
                        break

        # Group and display (matches live output format)
        groups = group_items(
            tcs,
            get_name=lambda tc: tc.get("function", {}).get("name", "?"),
            get_args=lambda tc: (
                json.loads(tc["function"]["arguments"])
                if isinstance(tc.get("function", {}).get("arguments"), str)
                else tc.get("function", {}).get("arguments") or {}
            ),
        )
        for name, summaries in groups:
            merged = merge_summaries(summaries)
            if name in errors:
                self.tool_fail(name, merged, errors[name])
            else:
                self.tool_done(name, merged, 0)

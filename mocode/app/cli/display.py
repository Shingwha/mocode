"""CLI display — output rendering, delegates input to Input and spinner to SpinnerRunner."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .display_helpers import group_items, merge_summaries
from .spinner import Priority, SpinnerRunner, Truncate
from .theme import (
    Style,
    Theme,
    _s,
)

if TYPE_CHECKING:
    from .input import Input


# ── Display ─────────────────────────────────────────────


class Display:
    """CLI display — output rendering. Input and spinner are delegated.

    Core rendering pipeline::

        Display.print()        — raw output with spinner line-clearing
        Display.render_line()  — styled output using Style instances
    """

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

    def print(self, *args, **kwargs) -> None:
        """Raw print with spinner line-clearing. Public API."""
        if self._spinner.active:
            self._spinner._clear_line()
        print(*args, **kwargs)

    def render_line(self, style: Style, text: str, *,
                    suffix: str = "", elapsed: float = -1) -> None:
        """Render a single styled line — the core output primitive.

        Args:
            style: Visual style (icon, colors)
            text: Main text content
            suffix: Optional dimmed context (e.g. tool args)
            elapsed: Optional elapsed seconds (shown if >= 0.1)
        """
        parts = []

        # Icon
        if style.icon:
            ic = style.icon_color or style.text_color
            parts.append(_s(style.icon, ic))

        # Text (with optional background)
        if style.bg:
            parts.append(_s(text, style.bg, style.text_color))
        else:
            parts.append(_s(text, style.text_color))

        # Suffix (dimmed)
        if suffix:
            from .theme import DIM
            parts.append(_s(suffix, DIM))

        # Elapsed time (dimmed)
        if elapsed >= 0.1:
            from .theme import DIM
            parts.append(_s(f"{elapsed:.1f}s", DIM))

        self.print(" ".join(parts))

    # ── Output: user message ──────────────────────────────

    def user_message(self, content: str) -> None:
        """Render a user message with dark background."""
        t = self.theme
        # Icon and text share the same background — combine them
        self.print(_s(f"{t.style_user.icon} {content.strip()}", t.style_user.bg, t.style_user.text_color))
        self.print()

    # ── Output: tool lifecycle ────────────────────────────

    def tool_done(self, name: str, merged: str, elapsed: float) -> None:
        """Print a successful tool line — ✓ with name, args, and optional elapsed."""
        self.render_line(self.theme.style_tool_done, name,
                         suffix=f"({merged})", elapsed=elapsed)

    def tool_fail(self, name: str, merged: str, error: str) -> None:
        """Print a failed tool line — ✗ with name, args, and error."""
        t = self.theme
        # Combine suffix and error on one line
        self.print(
            f"{_s('✗', t.style_tool_fail.icon_color or t.style_tool_fail.text_color)} "
            f"{_s(name, t.style_tool_fail.text_color)}"
            f"{_s(f'({merged}): ', t.style_tool_fail.text_color)}"
            f"{_s(error, t.style_tool_fail.icon_color or t.style_tool_fail.text_color)}"
        )

    # ── Output: model response ────────────────────────────

    def reasoning(self, content: str) -> None:
        """Render reasoning content (thinking)."""
        for line in content.splitlines():
            self.render_line(self.theme.style_reasoning, line)

    def text_response(self, content: str) -> None:
        """Render text response with line-by-line styling."""
        for line in content.strip().splitlines():
            self.render_line(self.theme.style_text, line)

    def response(self, text: str) -> None:
        """Render final response (raw text)."""
        self.print(f"\n{text}\n")

    # ── Output: status ────────────────────────────────────

    def usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.render_line(self.theme.style_usage, f"↑{prompt_tokens:,} ↓{completion_tokens:,}")

    def compact(self, old: int, new: int) -> None:
        self.render_line(self.theme.style_compact, f"Compacted: {old} → {new} msgs")

    def info(self, text: str) -> None:
        self.render_line(self.theme.style_info, text)

    def warn(self, text: str) -> None:
        self.render_line(self.theme.style_warn, text)

    def error(self, text: str) -> None:
        self.render_line(self.theme.style_error, text)

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
                    i = j
                else:
                    i += 1
            else:
                i += 1

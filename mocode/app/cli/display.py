"""CLI display — output rendering, tool summaries, and message re-rendering."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from ...core.tool import ERROR_PREFIXES, ToolRegistry
from ..text import ellipsize_middle
from .spinner import Priority, SpinnerRunner, Truncate
from .theme import DEFAULT_PALETTE, ColorPalette, DisplayStyles, SpinnerStyles, Style

if TYPE_CHECKING:
    from .input import Input

CONTENT_LIMIT = 60  # width budget for tool arguments inside parentheses


# ── Tool summaries ──────────────────────────────────────────


def tool_summary(name: str, args: dict, tools: ToolRegistry | None = None) -> str:
    """One-line argument summary for *name*, using its declared ``summary_key``."""
    if not args:
        return ""
    key = ""
    tool = tools.get(name) if tools is not None else None
    if tool is not None:
        key = tool.summary_key
    if not key or key not in args:
        key = next(iter(args))
    return ellipsize_middle(str(args.get(key, "")), CONTENT_LIMIT)


def merge_summaries(summaries: list[str]) -> str:
    """Join summaries with ', ', truncating at CONTENT_LIMIT with an '… +N' suffix."""
    if not summaries:
        return ""
    joined = ", ".join(summaries)
    if len(joined) <= CONTENT_LIMIT:
        return joined
    total = 0
    count = 0
    for s in summaries:
        add = len(s) + (2 if count > 0 else 0)
        if total + add > CONTENT_LIMIT - 10:
            break
        total += add
        count += 1
    count = max(count, 1)
    shown = ", ".join(summaries[:count])
    remaining = len(summaries) - count
    return shown + f"… +{remaining}" if remaining else shown


def group_pairs(
    pairs: Iterable[tuple[str, dict]], tools: ToolRegistry | None = None
) -> list[tuple[str, list[str]]]:
    """Group ``(tool_name, args)`` pairs by name, preserving first-seen order."""
    order: list[str] = []
    groups: dict[str, list[str]] = {}
    for name, args in pairs:
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(tool_summary(name, args, tools))
    return [(name, groups[name]) for name in order]


def group_tool_calls(tool_calls, tools: ToolRegistry | None = None):
    """Group OpenAI-style ToolCall objects by tool name."""
    return group_pairs(
        (
            (tc.name, _load_args(tc.arguments))
            for tc in tool_calls
        ),
        tools,
    )


def group_message_tool_calls(tcs: list[dict], tools: ToolRegistry | None = None):
    """Group tool calls as stored in message history."""
    return group_pairs(
        (
            (
                tc.get("function", {}).get("name", "?"),
                _load_args(tc.get("function", {}).get("arguments")),
            )
            for tc in tcs
        ),
        tools,
    )


def _load_args(arguments) -> dict:
    if isinstance(arguments, str):
        import json

        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {}
    return arguments or {}


def is_error_result(content: str) -> bool:
    """Whether a stored tool result records a failure (history re-rendering)."""
    return content.startswith(ERROR_PREFIXES)


# ── Display ─────────────────────────────────────────────────


class Display:
    """Terminal output — rendering only; input and spinner are delegated.

    Core primitives::

        Display.print()        raw output with spinner line-clearing
        Display.render_line()  styled output using Style instances

    Plugin events reach the screen through :meth:`render_event`; a plugin
    registers how its own event type looks with :meth:`add_event_renderer`.
    """

    def __init__(
        self,
        input_: Input,
        styles: DisplayStyles | None = None,
        palette: ColorPalette | None = None,
        spinner_styles: SpinnerStyles | None = None,
        spinner_palette: ColorPalette | None = None,
    ):
        self._s = styles or DisplayStyles()
        self._p = palette or DEFAULT_PALETTE
        self._input = input_
        self._spinner = SpinnerRunner(styles=spinner_styles, palette=spinner_palette)
        self._pending_input: str | None = None
        self._event_renderers: dict[type, Callable[[object], str]] = {}

    # ── Input delegation ──────────────────────────────────

    def set_pending_input(self, text: str) -> None:
        """Queue text to pre-fill the next prompt."""
        self._pending_input = text

    def clear_session(self) -> None:
        """Clear the paste store when starting a new conversation."""
        self._input.clear_session()

    async def prompt(self) -> str:
        default = self._pending_input or ""
        self._pending_input = None
        return await self._input.prompt(default=default)

    # ── Spinner delegation ────────────────────────────────

    def spinner_set(
        self,
        id: str,
        text: str = "",
        priority: int | Priority = Priority.NORMAL,
        truncate: Truncate | str = Truncate.TAIL,
    ) -> None:
        self._spinner.set(id, text, priority, truncate)

    def spinner_remove(self, id: str) -> None:
        self._spinner.remove(id)

    def spinner(self, style=None):
        return self._spinner.spin(style)

    # ── Plugin events ─────────────────────────────────────

    def add_event_renderer(
        self, event_type: type, render: Callable[[object], str]
    ) -> None:
        """Register how a plugin-defined event should be rendered to one line."""
        self._event_renderers[event_type] = render

    def render_event(self, event: object) -> None:
        """Render an event a hook emitted. Unknown types fall back to a dim line."""
        render = self._event_renderers.get(type(event))
        if render is None:
            for cls in type(event).__mro__[1:]:
                if cls in self._event_renderers:
                    render = self._event_renderers[cls]
                    break
        text = render(event) if render else f"{type(event).__name__}: {event}"
        self.render_line(self._s.info, text)

    # ── Output core ───────────────────────────────────────

    def print(self, *args, **kwargs) -> None:
        """Raw print that keeps the spinner line intact."""
        if self._spinner.active:
            self._spinner._clear_line()
        print(*args, **kwargs)

    def render_line(
        self,
        style: Style,
        text: str,
        *,
        suffix: str = "",
        elapsed: float = -1,
        error: str = "",
    ) -> None:
        """Render a single styled line — the core output primitive."""
        self.print(style.render(text, self._p, suffix=suffix, elapsed=elapsed, error=error))

    def _render_multiline(self, style: Style, content: str) -> None:
        for line in content.splitlines():
            self.render_line(style, line)

    def divider(self, style: str = "warning", width: int = 48) -> None:
        self.print(self._p.s("─" * width, style))

    # ── Output: messages ──────────────────────────────────

    def user_message(self, content: str) -> None:
        s = self._s.user_input
        self.print(self._p.s(f"{s.icon} {content.strip()}", s.bg, s.fg))
        self.print()

    def reasoning(self, content: str) -> None:
        self._render_multiline(self._s.reasoning, content)

    def text_response(self, content: str) -> None:
        self._render_multiline(self._s.text, content.strip())

    def response(self, text: str) -> None:
        self.print(f"\n{text}\n")

    # ── Output: tool lifecycle ────────────────────────────

    def tool_done(self, name: str, merged: str, elapsed: float) -> None:
        self.render_line(self._s.tool_done, name, suffix=f"({merged})", elapsed=elapsed)

    def tool_fail(self, name: str, merged: str, error: str, elapsed: float = -1) -> None:
        self.render_line(
            self._s.tool_fail,
            name,
            suffix=f"({merged})" if merged else "",
            error=error,
            elapsed=elapsed,
        )

    # ── Output: status ────────────────────────────────────

    def usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.render_line(self._s.usage, f"↑{prompt_tokens:,} ↓{completion_tokens:,}")

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

    # ── History re-rendering ──────────────────────────────

    def render_messages(self, messages: list[dict], tools: ToolRegistry | None = None) -> None:
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
                if msg.get("content") and msg.get("tool_calls"):
                    self.text_response(msg["content"])
                elif msg.get("content"):
                    self.response(msg["content"])
                tcs = msg.get("tool_calls", [])
                if tcs:
                    j = i + 1
                    tool_msgs = []
                    while j < len(messages) and messages[j].get("role") == "tool":
                        tool_msgs.append(messages[j])
                        j += 1
                    self._render_tool_calls(tcs, tool_msgs, tools)
                    i = j
                else:
                    i += 1
            else:
                i += 1

    def _render_tool_calls(
        self, tcs: list[dict], tool_messages: list[dict], tools: ToolRegistry | None
    ) -> None:
        errors: dict[str, str] = {}
        for msg in tool_messages:
            content = msg.get("content", "")
            if not is_error_result(content):
                continue
            for tc in tcs:
                if tc.get("id") == msg.get("tool_call_id"):
                    errors[tc.get("function", {}).get("name", "?")] = content[:80]
                    break

        for name, summaries in group_message_tool_calls(tcs, tools):
            merged = merge_summaries(summaries)
            if name in errors:
                self.tool_fail(name, merged, errors[name])
            else:
                self.tool_done(name, merged, 0)

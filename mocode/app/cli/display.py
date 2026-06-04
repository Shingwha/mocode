"""CLI display — output rendering, delegates input to Input and spinner to SpinnerRunner."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from .spinner import SpinnerRunner
from .theme import (
    BG_USER,
    BOLD,
    DIM,
    GRAY,
    GREEN,
    RED,
    SOFT_CYAN,
    YELLOW,
    Theme,
    _s,
)

from ..workflow.events import (
    LoopIterEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
    WorkflowEvent,
)

if TYPE_CHECKING:
    from ...workflow import Workflow
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
        args = (
            json.loads(tc.arguments)
            if isinstance(tc.arguments, str)
            else (tc.arguments or {})
        )
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


def _format_route(route) -> str:
    """Format a Route as a compact string for show view."""
    parts = []
    if route.match:
        parts.append(f"/{route.match}/")
    parts.append(", ".join(route.to))
    if route.max:
        parts.append(f"(max {route.max})")
    return " ".join(parts)


# ── Display ─────────────────────────────────────────────


class Display:
    """CLI display — output rendering. Input and spinner are delegated."""

    def __init__(self, input_: Input, theme: Theme | None = None):
        self.theme = theme or Theme()
        self._input = input_
        self._spinner = SpinnerRunner()
        self._wf_start_time: float = 0.0
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
        self._print(
            f"{_s(t.icon_tool, DIM)} {_s(name, t.color_tool)}{_s(f'({summary})', DIM)}"
        )

    def tool_start_batched(self, groups: list[tuple[str, list[str]]]):
        """Print merged tool call lines — one per tool type."""
        t = self.theme
        for name, summaries in groups:
            merged = _merge_summaries(summaries)
            self._print(
                f"{_s(t.icon_tool, DIM)} {_s(name, t.color_tool)}{_s(f'({merged})', DIM)}"
            )

    def tool_error(self, msg: str):
        self._styled(self.theme.icon_error, msg, self.theme.color_error)

    def tool_timeout(self, seconds: int):
        self._styled(
            self.theme.icon_error, f"timeout: {seconds}s", self.theme.color_error
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

    # ── Workflow event handling ───────────────────────────

    def handle_event(self, event: WorkflowEvent) -> None:
        """Single entry point for all workflow events."""
        if isinstance(event, WaveReadyEvent):
            self._on_wave_ready(event)
        elif isinstance(event, RouterConditionEvent):
            self._on_router_condition(event)
        elif isinstance(event, NodeSkippedEvent):
            self._on_node_skipped(event)
        elif isinstance(event, NodeStartEvent):
            self._on_node_start(event)
        elif isinstance(event, NodeDoneEvent):
            self._on_node_done(event)
        elif isinstance(event, LoopIterEvent):
            self._on_loop_iter(event)
        elif isinstance(event, ProgressEvent):
            self._on_progress(event)

    def _on_wave_ready(self, event: WaveReadyEvent) -> None:
        """Print wave header."""
        if event.wave_idx > 0:
            self._print()
        node_ids = event.node_ids
        if len(node_ids) <= 4:
            node_str = _s(", ".join(node_ids), DIM)
        else:
            shown = ", ".join(node_ids[:3])
            node_str = _s(f"{shown} +{len(node_ids) - 3} more", DIM)
        self._print(
            f"{_s('◇', YELLOW)} Wave {event.wave_idx + 1}/{event.total_waves}  {node_str}"
        )

    def _on_router_condition(self, event: RouterConditionEvent) -> None:
        """Print router condition — always in router's wave context."""
        if event.matched and event.targets:
            target_str = ", ".join(event.targets)
            self._print(
                f"  └─ {_s('▸', SOFT_CYAN)} {_s(event.router_id, SOFT_CYAN)} → {_s(target_str, DIM)}"
            )
        else:
            self._print(
                f"  └─ {_s('▹', GRAY)} {_s(event.router_id, GRAY)} · {_s('no match', DIM)}"
            )

    def _on_node_skipped(self, event: NodeSkippedEvent) -> None:
        """Print a skipped node line."""
        icon, label = self._SKIP_STYLES.get(event.reason, ("∘", event.reason))
        self._print(
            f"  │  {_s(icon, GRAY)} {_s(event.node_id, GRAY)}  {_s(label, DIM)}"
        )

    def _on_node_start(self, event: NodeStartEvent) -> None:
        """Update spinner detail to show the currently running node."""
        if event.description:
            self.set_spinner_detail(f"{event.node_id}: {event.description}")
        else:
            self.set_spinner_detail(f"running: {event.node_id}")

    def _on_node_done(self, event: NodeDoneEvent) -> None:
        """Print a completed node result line."""
        dur = f"{event.result.duration:.1f}s"
        icon = _s("✓", GREEN) if event.result.exit_code == 0 else _s("✗", RED)
        desc = event.description or (
            event.result.task[:40] if event.result.task else ""
        )
        iter_suffix = (
            f" (iter {event.result.iteration})" if event.result.iteration > 1 else ""
        )
        self._print(
            f"  └─ {icon} {_s(event.node_id, BOLD)} · {desc}{iter_suffix}  {_s(dur, DIM)}"
        )

    def _on_loop_iter(self, event: LoopIterEvent) -> None:
        """Print a loop iteration line."""
        dur = f"{event.result.duration:.1f}s"
        desc = event.description or (
            event.result.task[:40] if event.result.task else ""
        )
        max_str = str(event.max_iter) if event.max_iter > 0 else "∞"
        self._print(
            f"  └─ {_s('↻', YELLOW)} {_s(event.node_id, BOLD)} [{event.iteration}/{max_str}] · {desc}  {_s(dur, DIM)}"
        )

    def _on_progress(self, event: ProgressEvent) -> None:
        """Update spinner detail with progress message."""
        self.set_spinner_detail(event.message)

    _SKIP_STYLES: dict[str, tuple[str, str]] = {
        "not activated by router": ("○", "routed elsewhere"),
        "dependency skipped": ("◌", "upstream skipped"),
        "not activated": ("∘", "not reached"),
    }

    # ── Output: workflow lifecycle ────────────────────────

    def workflow_start(self, wf: Workflow) -> None:
        """Print workflow header before execution."""
        node_count = wf.total_nodes()
        self._wf_start_time = time.monotonic()
        self._print(
            f"{_s('●', YELLOW)} {_s(wf.name, BOLD)}  {_s(f'{node_count} nodes', DIM)}"
        )
        self._print()

    def workflow_summary(self, wf: Workflow, results: list | None = None) -> None:
        """Print final output and summary."""
        results = results or []

        passed = sum(1 for r in results if r.exit_code == 0 and r.status == "done")
        failed = sum(1 for r in results if r.exit_code != 0)
        skipped_count = sum(1 for r in results if r.status == "skipped")
        wall_time = (
            time.monotonic() - self._wf_start_time if self._wf_start_time else 0.0
        )

        # Final output — find the last result with meaningful content
        last_output_result = None
        for r in reversed(results):
            if r.output and r.status == "done":
                last_output_result = r
                break
        if last_output_result:
            self._print(_s("─" * 48, YELLOW))
            for ol in last_output_result.output.splitlines():
                self._print(ol)
        # Show errors from any failed result
        for r in results:
            if r.error and r.exit_code != 0:
                if not last_output_result:
                    self._print(_s("─" * 48, YELLOW))
                    last_output_result = r  # just to avoid double separator
                self._print(_s(f"Error: {r.error}", RED))

        # Summary
        self._print()
        time_str = _s(f"{wall_time:.1f}s", DIM)
        summary_icon_color = GREEN if failed == 0 else RED
        parts = []
        if passed:
            parts.append(_s(f"{passed} passed", GREEN + BOLD))
        if failed:
            parts.append(_s(f"{failed} failed", RED))
        if skipped_count:
            parts.append(_s(f"{skipped_count} skipped", DIM))
        stat_str = " · ".join(parts)

        self._print(
            f"{_s('■', summary_icon_color)} {_s(wf.name, BOLD)}  {stat_str}  {time_str}"
        )

    # ── Output: workflow show / list ──────────────────────

    def workflow_show(self, wf: Workflow) -> str:
        """Generate a DAG tree-style structural view of the workflow."""
        lines = [
            f"{_s(wf.name, BOLD)}",
            f"  {wf.description}" if wf.description else "",
            f"  {_s(f'{wf.total_nodes()} nodes', DIM)}",
            "",
        ]
        node_map = wf.node_map
        dependents = wf.dependents

        # Track which nodes have been rendered
        rendered: set[str] = set()

        def _render_tree(node_id: str, prefix: str, is_last: bool) -> None:
            if node_id in rendered:
                lines.append(f"{prefix}└─ (→ {node_id})")
                return
            rendered.add(node_id)
            node = node_map[node_id]
            connector = "└─" if is_last else "├─"
            preview = (
                node.description
                if node.description
                else (node.task[:50] if node.task else "")
            )

            if node.type == "router":
                route_strs = [_format_route(r) for r in node.routes]
                lines.append(
                    f"{prefix}{connector} {_s(node_id, SOFT_CYAN)} · router  {_s('→', YELLOW)} {' | '.join(route_strs)}"
                )
            else:
                lines.append(f"{prefix}{connector} {_s(node_id, BOLD)} · {preview}")

            children = dependents.get(node_id, [])
            child_prefix = prefix + ("   " if is_last else "│  ")
            for ci, child_id in enumerate(children):
                _render_tree(child_id, child_prefix, ci == len(children) - 1)

        # Start from root nodes
        roots = wf.root_nodes
        for ri, root in enumerate(roots):
            _render_tree(root.id, "", ri == len(roots) - 1)

        return "\n".join(lines)

    def workflow_list(self, workflows: list[Workflow]) -> str:
        """Generate a compact workflow list — name + brief description."""
        lines = []
        for wf in workflows:
            desc = wf.description[:50] if wf.description else "(no description)"
            lines.append(f"  {_s(wf.name, BOLD):<20} {_s(desc, DIM)}")
        return "\n".join(lines)

    # ── Screen ────────────────────────────────────────────

    def clear_screen(self):
        import os

        os.system("cls" if os.name == "nt" else "clear")

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

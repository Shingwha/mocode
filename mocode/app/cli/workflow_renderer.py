"""Workflow rendering — event display, DAG tree, and execution summary."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .spinner import Priority, Truncate
from .theme import (
    BOLD,
    DIM,
    GRAY,
    GREEN,
    RED,
    SOFT_CYAN,
    YELLOW,
    _s,
)

from ..workflow.events import (
    LoopIterEvent,
    MapFanOutEvent,
    MapItemDoneEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    NodeToolBatchDoneEvent,
    NodeToolCallEvent,
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
    WorkflowEvent,
)

from ..workflow.models import NodeResult

if TYPE_CHECKING:
    from ..workflow import Workflow
    from .display import Display


# ── Skip reason styles ────────────────────────────────────

_SKIP_STYLES: dict[str, tuple[str, str]] = {
    "not activated by router": ("○", "routed elsewhere"),
    "dependency skipped": ("◌", "upstream skipped"),
    "not activated": ("∘", "not reached"),
}


# ── Helpers ────────────────────────────────────────────────


def _format_route(route) -> str:
    """Format a Route as a compact string for show view."""
    parts = []
    if route.match:
        parts.append(f"/{route.match}/")
    parts.append(", ".join(route.to))
    if route.max:
        parts.append(f"(max {route.max})")
    return " ".join(parts)


# ── WorkflowRenderer ───────────────────────────────────────


class WorkflowRenderer:
    """Renders workflow events, DAG trees, and execution summaries."""

    def __init__(self, display: Display) -> None:
        self._d = display
        self._start_time: float = 0.0

    # ── Event dispatch ─────────────────────────────────────

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
        elif isinstance(event, NodeToolCallEvent):
            self._on_tool_call(event)
        elif isinstance(event, NodeToolBatchDoneEvent):
            self._on_tool_batch_done(event)
        elif isinstance(event, NodeDoneEvent):
            self._on_node_done(event)
        elif isinstance(event, MapFanOutEvent):
            self._on_map_fan_out(event)
        elif isinstance(event, MapItemDoneEvent):
            self._on_map_item_done(event)
        elif isinstance(event, LoopIterEvent):
            self._on_loop_iter(event)
        elif isinstance(event, ProgressEvent):
            self._on_progress(event)

    # ── Event handlers ─────────────────────────────────────

    def _on_wave_ready(self, event: WaveReadyEvent) -> None:
        if event.wave_idx > 0:
            self._d.print()
        node_ids = event.node_ids
        if len(node_ids) <= 4:
            node_str = _s(", ".join(node_ids), DIM)
        else:
            shown = ", ".join(node_ids[:3])
            node_str = _s(f"{shown} +{len(node_ids) - 3} more", DIM)
        self._d.print(
            f"{_s('◇', YELLOW)} Wave {event.wave_idx + 1}/{event.total_waves}  {node_str}"
        )

    def _on_router_condition(self, event: RouterConditionEvent) -> None:
        if event.matched and event.targets:
            target_str = ", ".join(event.targets)
            self._d.print(
                f"  └─ {_s('▸', SOFT_CYAN)} {_s(event.router_id, SOFT_CYAN)} → {_s(target_str, DIM)}"
            )
        else:
            self._d.print(
                f"  └─ {_s('▹', GRAY)} {_s(event.router_id, GRAY)} · {_s('no match', DIM)}"
            )

    def _on_node_skipped(self, event: NodeSkippedEvent) -> None:
        icon, label = _SKIP_STYLES.get(event.reason, ("∘", event.reason))
        self._d.print(
            f"  │  {_s(icon, GRAY)} {_s(event.node_id, GRAY)}  {_s(label, DIM)}"
        )

    def _on_node_start(self, event: NodeStartEvent) -> None:
        desc = event.description or ""
        node_prefix = _s(event.node_id, BOLD)
        if desc:
            self._d.print(f"{_s('▸', BOLD)} {node_prefix}:start ({desc})")
        else:
            self._d.print(f"{_s('▸', BOLD)} {node_prefix}:start")
        # Spinner: node_id · Thinking
        self._d.spinner_set("wf_node", event.node_id,
                            priority=Priority.NORMAL, truncate=Truncate.TAIL)
        self._d.spinner_set("wf_thinking", "Thinking",
                            priority=Priority.LOW, truncate=Truncate.TAIL)

    def _on_tool_call(self, event: NodeToolCallEvent) -> None:
        from .display import tool_summary
        summary = tool_summary(event.tool_name, event.tool_args)
        node_prefix = f"{event.node_id}:{event.tool_name}"
        if event.error:
            self._d.render_line(
                self._d.theme.style_tool_fail, node_prefix,
                suffix=f"({summary})" if summary else "",
                error=event.error, elapsed=event.elapsed,
            )
        else:
            self._d.render_line(
                self._d.theme.style_tool_done, node_prefix,
                suffix=f"({summary})" if summary else "",
                elapsed=event.elapsed,
            )

    def _on_tool_batch_done(self, event: NodeToolBatchDoneEvent) -> None:
        # Clear tool spinner segments
        self._d.spinner_remove("wf_tools_tag")
        self._d.spinner_remove("wf_tools_detail")
        # Restore Thinking spinner
        self._d.spinner_set("wf_thinking", "Thinking",
                            priority=Priority.LOW, truncate=Truncate.TAIL)

    def _on_node_done(self, event: NodeDoneEvent) -> None:
        dur = f"{event.result.duration:.1f}s"
        icon = _s("■", GREEN) if event.result.exit_code == 0 else _s("■", RED)
        node_prefix = _s(event.node_id, BOLD)
        desc = event.description or (event.result.task[:40] if event.result.task else "")
        # Build detail inside parentheses
        parts = [desc]
        if event.result.iteration > 1:
            parts.append(f"iter {event.result.iteration}")
        if event.result.error and event.result.exit_code != 0:
            parts.append(f"{_s('ERROR', RED)} {event.result.error[:30]}")
        detail = ", ".join(parts)
        self._d.print(f"{icon} {node_prefix}:done ({detail})  {_s(dur, DIM)}")
        # Clear all spinner segments
        self._d.spinner_remove("wf_node")
        self._d.spinner_remove("wf_thinking")
        self._d.spinner_remove("wf_tools_tag")
        self._d.spinner_remove("wf_tools_detail")

    def _on_loop_iter(self, event: LoopIterEvent) -> None:
        dur = f"{event.result.duration:.1f}s"
        desc = event.description or (
            event.result.task[:40] if event.result.task else ""
        )
        max_str = str(event.max_iter) if event.max_iter > 0 else "∞"
        self._d.print(
            f"  └─ {_s('↻', YELLOW)} {_s(event.node_id, BOLD)} [{event.iteration}/{max_str}] · {desc}  {_s(dur, DIM)}"
        )

    def _on_map_fan_out(self, event: MapFanOutEvent) -> None:
        self._d.print(
            f"  └─ {_s('⊞', SOFT_CYAN)} {_s(event.map_id, BOLD)} · "
            f"{_s(f'{event.item_count} items', DIM)}"
        )

    def _on_map_item_done(self, event: MapItemDoneEvent) -> None:
        dur = f"{event.duration:.1f}s"
        val = event.item_value[:30] + ("…" if len(event.item_value) > 30 else "")
        self._d.print(
            f"     {_s('▪', GRAY)} [{event.item_index + 1}/{event.total_count}] "
            f"{_s(val, DIM)}  {_s(dur, DIM)}"
        )

    def _on_progress(self, event: ProgressEvent) -> None:
        if event.node_id:
            self._d.spinner_set("wf_node", event.node_id,
                                priority=Priority.NORMAL, truncate=Truncate.TAIL)
            self._d.spinner_set("wf_thinking", event.detail or event.message,
                                priority=Priority.LOW, truncate=Truncate.TAIL)
        else:
            self._d.spinner_set("wf_thinking", event.message,
                                priority=Priority.LOW, truncate=Truncate.TAIL)

    # ── Lifecycle rendering ────────────────────────────────

    def start(self, wf: Workflow) -> None:
        """Print workflow header before execution."""
        node_count = wf.total_nodes()
        self._start_time = time.monotonic()
        self._d.print(
            f"{_s('●', YELLOW)} {_s(wf.name, BOLD)}  {_s(f'{node_count} nodes', DIM)}"
        )
        self._d.print()

    def summary(self, wf: Workflow, results: list | None = None) -> None:
        """Print final output and summary."""
        results = results or []

        passed = sum(1 for r in results if r.exit_code == 0 and r.status == "done")
        failed = sum(1 for r in results if r.exit_code != 0)
        skipped_count = sum(1 for r in results if r.status == "skipped")
        wall_time = (
            time.monotonic() - self._start_time if self._start_time else 0.0
        )

        # Final output — find the last result with meaningful content
        last_output_result = None
        for r in reversed(results):
            if r.output and r.status == "done":
                last_output_result = r
                break
        if last_output_result:
            self._d.print(_s("─" * 48, YELLOW))
            for ol in last_output_result.output.splitlines():
                self._d.print(ol)
        # Show errors from any failed result
        for r in results:
            if r.error and r.exit_code != 0:
                if not last_output_result:
                    self._d.print(_s("─" * 48, YELLOW))
                    last_output_result = r
                self._d.print(_s(f"Error: {r.error}", RED))

        # Summary
        self._d.print()
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

        self._d.print(
            f"{_s('■', summary_icon_color)} {_s(wf.name, BOLD)}  {stat_str}  {time_str}"
        )

    # ── Static views ───────────────────────────────────────

    def show(self, wf: Workflow) -> str:
        """Generate a DAG tree-style structural view of the workflow."""
        lines = [
            f"{_s(wf.name, BOLD)}",
            f"  {wf.description}" if wf.description else "",
            f"  {_s(f'{wf.total_nodes()} nodes', DIM)}",
            "",
        ]
        node_map = wf.node_map
        dependents = wf.dependents

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

        roots = wf.root_nodes
        for ri, root in enumerate(roots):
            _render_tree(root.id, "", ri == len(roots) - 1)

        return "\n".join(lines)

    def list_workflows(self, workflows: list[Workflow]) -> str:
        """Generate a compact workflow list — name + brief description."""
        lines = []
        for wf in workflows:
            desc = wf.description[:50] if wf.description else "(no description)"
            lines.append(f"  {_s(wf.name, BOLD):<20} {_s(desc, DIM)}")
        return "\n".join(lines)


# ── Summary helpers (standalone, operate on results lists) ──


def summarize(name: str, results: list[NodeResult]) -> str:
    """Compact one-line-per-result summary."""
    lines = [f"Workflow: {name}"]
    for r in results:
        status = "OK" if r.exit_code == 0 else "FAIL"
        task_preview = r.task[:40] if r.task else "(empty)"
        iter_suffix = f" (iter {r.iteration})" if r.iteration > 1 else ""
        lines.append(
            f"  [{status}] {r.node_id}{iter_suffix} · {task_preview}: {r.duration:.1f}s"
        )
    return "\n".join(lines)


def detailed_summarize(
    name: str, results: list[NodeResult], max_lines: int = 10
) -> str:
    """Multi-line summary with output and error excerpts."""
    lines = [f"Workflow: {name}"]
    for r in results:
        status = "OK" if r.exit_code == 0 else "FAIL"
        task_preview = r.task[:60] if r.task else "(empty)"
        iter_suffix = f" (iter {r.iteration})" if r.iteration > 1 else ""
        lines.append(
            f"  [{status}] {r.node_id}{iter_suffix} · {task_preview} ({r.duration:.1f}s)"
        )
        if r.output:
            output_lines = r.output.splitlines()
            for ol in output_lines[:max_lines]:
                lines.append(f"      {ol}")
            if len(output_lines) > max_lines:
                lines.append(f"      ... ({len(output_lines) - max_lines} more lines)")
        if r.error:
            lines.append(f"      Error: {r.error[:100]}")
    return "\n".join(lines)

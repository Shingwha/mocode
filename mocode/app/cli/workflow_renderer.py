"""Workflow rendering — event display, DAG tree, and execution summary."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..utils import tool_summary
from .display import merge_summaries
from .formatter import (
    _SKIP_STYLES,
    _format_route,
    detailed_summarize,
    summarize,
)
from .palette import DEFAULT_PALETTE, ColorPalette
from .spinner import Priority, Truncate
from .styles import WorkflowStyles
from .style import Style

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
from ..workflow.run_store import _pid_exists

if TYPE_CHECKING:
    from ..workflow import Workflow
    from .display import Display


# ── Event dispatch table ─────────────────────────────────

_EVENT_HANDLERS: dict[type, str] = {
    WaveReadyEvent: "_on_wave_ready",
    RouterConditionEvent: "_on_router_condition",
    NodeSkippedEvent: "_on_node_skipped",
    NodeStartEvent: "_on_node_start",
    NodeToolCallEvent: "_on_tool_call",
    NodeToolBatchDoneEvent: "_on_tool_batch_done",
    NodeDoneEvent: "_on_node_done",
    MapFanOutEvent: "_on_map_fan_out",
    MapItemDoneEvent: "_on_map_item_done",
    LoopIterEvent: "_on_loop_iter",
    ProgressEvent: "_on_progress",
}


# ── WorkflowRenderer ───────────────────────────────────────


class WorkflowRenderer:
    """Renders workflow events, DAG trees, and execution summaries."""

    def __init__(self, display: Display,
                 styles: WorkflowStyles | None = None,
                 palette: ColorPalette | None = None) -> None:
        self._d = display
        self._s = styles or WorkflowStyles()
        self._p = palette or DEFAULT_PALETTE
        self._start_time: float = 0.0
        self._pending_tool_batch = False
        self._batch_tool_groups: dict[str, list[str]] = {}  # name -> [summary]

    # ── Event dispatch ─────────────────────────────────────

    def handle_event(self, event: WorkflowEvent) -> None:
        """Single entry point for all workflow events."""
        handler_name = _EVENT_HANDLERS.get(type(event))
        if handler_name:
            getattr(self, handler_name)(event)

    # ── Event handlers ─────────────────────────────────────

    def _on_wave_ready(self, event: WaveReadyEvent) -> None:
        if event.wave_idx > 0:
            self._d.print()
        node_ids = event.node_ids
        if len(node_ids) <= 4:
            node_str = self._p.s(", ".join(node_ids), "dim")
        else:
            shown = ", ".join(node_ids[:3])
            node_str = self._p.s(f"{shown} +{len(node_ids) - 3} more", "dim")
        line = self._s.wave.render(
            f"Wave {event.wave_idx + 1}/{event.total_waves}",
            self._p,
            suffix=node_str,
        )
        self._d.print(line)

    def _on_router_condition(self, event: RouterConditionEvent) -> None:
        if event.matched and event.targets:
            target_str = ", ".join(event.targets)
            line = (
                f"  └─ {self._s.router_match.render(event.router_id, self._p)}"
                f" → {self._p.s(target_str, 'dim')}"
            )
            self._d.print(line)
        else:
            line = (
                f"  └─ {self._s.router_miss.render(event.router_id, self._p)}"
                f" · {self._p.s('no match', 'dim')}"
            )
            self._d.print(line)

    def _on_node_skipped(self, event: NodeSkippedEvent) -> None:
        icon, label = _SKIP_STYLES.get(event.reason, ("∘", event.reason))
        line = (
            f"  │  {self._s.skip.render(event.node_id, self._p)}"
            f"  {self._p.s(label, 'dim')}"
        )
        self._d.print(line)

    def _on_node_start(self, event: NodeStartEvent) -> None:
        desc = f" ({event.description})" if event.description else ""
        line = self._s.node_start.render(
            f"{event.node_id}:start", self._p,
            suffix=desc,
        )
        self._d.print(line)
        # Spinner: node_id · Thinking
        self._d.spinner_set("wf_node", event.node_id,
                            priority=Priority.HIGH, truncate=Truncate.NONE)
        self._d.spinner_set("wf_thinking", "Thinking",
                            priority=Priority.NORMAL, truncate=Truncate.TAIL)

    def _on_tool_call(self, event: NodeToolCallEvent) -> None:
        # On first tool call of a batch, switch spinner from Thinking to tools
        if not self._pending_tool_batch:
            self._pending_tool_batch = True
            self._d.spinner_remove("wf_thinking")
            self._batch_tool_groups = {}
        # Accumulate tool call info and update spinner in real time
        summary = tool_summary(event.tool_name, event.tool_args)
        self._batch_tool_groups.setdefault(event.tool_name, []).append(summary)
        self._update_spinner_for_tools(event.node_id, self._batch_tool_groups)

    def _on_tool_batch_done(self, event: NodeToolBatchDoneEvent) -> None:
        # Clear tool spinner segments
        self._d.spinner_remove("wf_tools_tag")
        self._d.spinner_remove("wf_tools_detail")
        # Restore Thinking spinner
        self._d.spinner_set("wf_thinking", "Thinking",
                            priority=Priority.NORMAL, truncate=Truncate.TAIL)
        self._pending_tool_batch = False
        self._batch_tool_groups = {}
        # Render grouped tool calls (matches CLI batch display)
        for name, summaries in event.groups:
            merged = merge_summaries(summaries)
            elapsed = event.elapsed.get(name, 0)
            node_prefix = f"{event.node_id}:{name}"
            if name in event.errors:
                self._d.tool_fail(node_prefix, merged, event.errors[name], elapsed)
            else:
                self._d.tool_done(node_prefix, merged, elapsed)

    def _on_node_done(self, event: NodeDoneEvent) -> None:
        dur = f"{event.result.duration:.1f}s"
        ok_style = self._s.node_done_ok
        fail_style = self._s.node_done_fail
        style = ok_style if event.result.exit_code == 0 else fail_style
        # Build usage detail
        parts = []
        tc = getattr(event.result, 'tool_calls', 0) or 0
        if tc > 0:
            parts.append(f"{tc} tools")
        pt = getattr(event.result, 'prompt_tokens', 0) or 0
        ct = getattr(event.result, 'completion_tokens', 0) or 0
        if pt > 0:
            parts.append(f"↑{pt:,} ↓{ct:,} tokens")
        if event.result.error and event.result.exit_code != 0:
            parts.append(f"{self._p.s('ERROR', 'error')} {event.result.error[:30]}")
        detail = f"  {' · '.join(parts)}" if parts else ""
        line = style.render(
            f"{event.node_id}:done", self._p,
            suffix=dur, error="" if event.result.exit_code == 0 else detail.lstrip(),
        )
        # Append non-error details as plain dim text
        if event.result.exit_code == 0 and detail:
            line += f" {self._p.s(detail.strip(), 'dim')}"
        self._d.print(line)
        # Clear all spinner segments
        self._d.spinner_remove("wf_node")
        self._d.spinner_remove("wf_thinking")
        self._d.spinner_remove("wf_tools_tag")
        self._d.spinner_remove("wf_tools_detail")
        self._batch_tool_groups = {}

    def _update_spinner_for_tools(self, node_id: str, groups: dict[str, list[str]]) -> None:
        """Update spinner segments to show running tools — same format as CLI."""
        total = sum(len(s) for s in groups.values())
        label = "running 1 tool" if total == 1 else f"running {total} tools"

        parts = []
        for name, summaries in groups.items():
            count = len(summaries)
            if total == 1:
                parts.append(f"{name}({merge_summaries(summaries)})")
            elif count > 1:
                parts.append(f"{name}×{count}")
            else:
                parts.append(name)

        self._d.spinner_set("wf_tools_tag", f"{node_id}: {label}",
                            priority=Priority.HIGH, truncate=Truncate.TAIL)
        self._d.spinner_set("wf_tools_detail", ", ".join(parts),
                            priority=Priority.LOW, truncate=Truncate.MIDDLE)

    def _on_loop_iter(self, event: LoopIterEvent) -> None:
        dur = f"{event.result.duration:.1f}s"
        desc = event.description or (
            event.result.task[:40] if event.result.task else ""
        )
        max_str = str(event.max_iter) if event.max_iter > 0 else "∞"
        self._d.print(
            f"  └─ {self._s.loop.render(f'{event.node_id}', self._p)} "
            f"[{event.iteration}/{max_str}] · {desc}  {self._p.s(dur, 'dim')}"
        )

    def _on_map_fan_out(self, event: MapFanOutEvent) -> None:
        self._d.print(
            f"  └─ {self._s.fanout.render(event.map_id, self._p)} · "
            f"{self._p.s(f'{event.item_count} items', 'dim')}"
        )

    def _on_map_item_done(self, event: MapItemDoneEvent) -> None:
        dur = f"{event.duration:.1f}s"
        val = event.item_value[:30] + ("…" if len(event.item_value) > 30 else "")
        # Build usage detail (same logic as _on_node_done)
        parts = []
        tc = getattr(event, 'tool_calls', 0) or 0
        if tc > 0:
            parts.append(f"{tc} tools")
        pt = getattr(event, 'prompt_tokens', 0) or 0
        ct = getattr(event, 'completion_tokens', 0) or 0
        if pt > 0:
            parts.append(f"↑{pt:,} ↓{ct:,} tokens")
        usage = f"  {' · '.join(parts)}" if parts else ""
        self._d.print(
            f"     {self._s.map_item.render(f'[{event.item_index + 1}/{event.total_count}]', self._p)} "
            f"{self._p.s(val, 'dim')}  {self._p.s(dur, 'dim')}{usage}"
        )

    def _on_progress(self, event: ProgressEvent) -> None:
        if not event.node_id:
            self._d.spinner_set("wf_thinking", event.message,
                                priority=Priority.NORMAL, truncate=Truncate.TAIL)
            return
        self._d.spinner_set("wf_node", event.node_id,
                            priority=Priority.HIGH, truncate=Truncate.NONE)
        self._d.spinner_set("wf_thinking", event.detail or event.message,
                            priority=Priority.NORMAL, truncate=Truncate.TAIL)

    # ── Lifecycle rendering ────────────────────────────────

    def start(self, wf: Workflow) -> None:
        """Print workflow header before execution."""
        node_count = wf.total_nodes()
        self._start_time = time.monotonic()
        self._d.print(
            self._s.header.render(
                wf.name, self._p,
                suffix=f"{node_count} nodes",
            )
        )
        self._d.print()

    @staticmethod
    def _collect_stats(results: list) -> tuple[int, int, int]:
        """Return (passed, failed, skipped) counts."""
        passed = sum(1 for r in results if r.exit_code == 0 and r.status == "done")
        failed = sum(1 for r in results if r.exit_code != 0)
        skipped = sum(1 for r in results if r.status == "skipped")
        return passed, failed, skipped

    @staticmethod
    def _build_usage_line(results: list) -> str:
        """Build dim usage totals line from done results."""
        done = [r for r in results if r.status == "done"]
        total_tools = sum(r.tool_calls for r in done)
        total_prompt = sum(r.prompt_tokens for r in done)
        total_completion = sum(r.completion_tokens for r in done)
        parts = []
        if total_tools > 0:
            parts.append(f"{total_tools} tools")
        if total_prompt > 0:
            parts.append(f"↑{total_prompt:,} ↓{total_completion:,} tokens")
        return f"  {' · '.join(parts)}" if parts else ""

    def summary(self, wf: Workflow, results: list | None = None) -> None:
        """Print final output and summary."""
        results = results or []
        passed, failed, skipped_count = self._collect_stats(results)
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
            self._d.print(self._p.s("─" * 48, "warning"))
            for ol in last_output_result.output.splitlines():
                self._d.print(ol)
        # Show errors from any failed result
        for r in results:
            if not r.error or r.exit_code == 0:
                continue
            if not last_output_result:
                self._d.print(self._p.s("─" * 48, "warning"))
                last_output_result = r
            self._d.print(self._p.s(f"Error: {r.error}", "error"))

        # Summary line
        self._d.print()
        stat_str = " · ".join(s for s in (
            self._p.s(f"{passed} passed", "success", "bold") if passed else None,
            self._p.s(f"{failed} failed", "error") if failed else None,
            self._p.s(f"{skipped_count} skipped", "dim") if skipped_count else None,
        ) if s)

        usage_str = self._build_usage_line(results)
        icon_style = self._s.node_done_ok if failed == 0 else self._s.node_done_fail
        self._d.print(
            f"{icon_style.render(wf.name, self._p)}  {stat_str}  "
            f"{self._p.s(f'{wall_time:.1f}s', 'dim')}"
            f"{self._p.s(usage_str, 'dim') if usage_str else ''}"
        )

    def cancelled(self, wf: Workflow, results: list | None = None) -> None:
        """Display cancellation message with partial results summary."""
        self._d.warn(f"Workflow '{wf.name}' cancelled by user.")
        if results:
            self.summary(wf, results)

    # ── Static views ───────────────────────────────────────

    def _render_node_content(self, node, connector: str, prefix: str) -> str:
        """Render a single node's display line for the DAG tree."""
        if node.type == "router":
            route_strs = [_format_route(r) for r in node.routes]
            return (
                f"{prefix}{connector} {self._p.s(node.id, 'info')} · router  "
                f"{self._p.s('→', 'warning')} {' | '.join(route_strs)}"
            )
        # Map node (each + as) — show fan-out mode
        if getattr(node, 'each', ''):
            as_var = getattr(node, 'as_', '') or '?'
            preview = (
                node.description
                if node.description
                else (node.task[:50] if node.task else "")
            )
            return (
                f"{prefix}{connector} {self._p.s(node.id, 'bold')} · "
                f"each {node.each} as {as_var} → {preview}"
            )
        preview = (
            node.description
            if node.description
            else (node.task[:50] if node.task else "")
        )
        return f"{prefix}{connector} {self._p.s(node.id, 'bold')} · {preview}"

    def show(self, wf: Workflow) -> str:
        """Generate a DAG tree-style structural view of the workflow."""
        lines = [
            f"{self._p.s(wf.name, 'bold')}",
            f"  {wf.description}" if wf.description else "",
            f"  {self._p.s(f'{wf.total_nodes()} nodes', 'dim')}",
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
            lines.append(self._render_node_content(node, connector, prefix))

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
            lines.append(f"  {self._p.s(wf.name, 'bold'):<20} {self._p.s(desc, 'dim')}")
        return "\n".join(lines)

    def list_runs(self, runs: list[dict], store: object) -> str:
        """Render recent run list."""
        lines = []
        for r in runs:
            rid = r.get("run_id", "?")
            rname = r.get("workflow_name", "?")
            status = r.get("status", "?")
            started = r.get("started_at", "?")[:19]

            alive_tag = ""
            if status == "running" and r.get("pid"):
                alive_tag = " (alive)" if store.is_alive(rid) else " (dead)"

            lines.append(f"  {rid}  {rname:20s}  {status}{alive_tag}  {started}")
        return "\n".join(lines)

    def run_detail(self, record: dict, status: str) -> str:
        """Render a single run's detailed status and results."""
        run_id = record.get("run_id", "?")
        name = record.get("workflow_name", "?")

        lines = [
            f"{self._p.s('●', 'bold')} {self._p.s(run_id, 'bold')}  {self._p.s(name, 'dim')}",
            f"  Status:    {status}",
            f"  Started:   {record.get('started_at', '?')}",
        ]

        if record.get("finished_at"):
            lines.append(f"  Finished:  {record['finished_at']}")
        if record.get("wall_duration") is not None:
            lines.append(f"  Duration:  {record['wall_duration']:.1f}s")
        if record.get("pid"):
            alive = "alive" if _pid_exists(record["pid"]) else "dead"
            lines.append(f"  PID:       {record['pid']} ({alive})")

        results = [NodeResult(**r) for r in record.get("results", [])]
        if results:
            lines.append("")
            lines.append(detailed_summarize(name, results, max_lines=9999))
        else:
            lines.append("  No node results yet.")

        if status == "crashed":
            lines.append("")
            lines.append(self._p.s("Process died unexpectedly. Check the log file.", "error"))

        return "\n".join(lines)

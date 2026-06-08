"""Workflow formatting helpers — pure functions for formatting routes, summaries, and skip reasons."""

from __future__ import annotations

from ..workflow.models import NodeResult


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


# ── Summary helpers ────────────────────────────────────────


def _usage_suffix(r: NodeResult) -> str:
    """Build compact usage suffix: '(5 tools, ↑12,000 ↓3,500)'."""
    parts = []
    if r.tool_calls > 0:
        parts.append(f"{r.tool_calls} tools")
    if r.prompt_tokens > 0:
        parts.append(f"↑{r.prompt_tokens:,} ↓{r.completion_tokens:,}")
    return f" ({', '.join(parts)})" if parts else ""


def summarize(name: str, results: list[NodeResult]) -> str:
    """Compact one-line-per-result summary."""
    lines = [f"Workflow: {name}"]
    for r in results:
        status = "OK" if r.exit_code == 0 else "FAIL"
        task_preview = r.task[:40] if r.task else "(empty)"
        lines.append(
            f"  [{status}] {r.node_id}{_usage_suffix(r)} · {task_preview}: {r.duration:.1f}s"
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
        lines.append(
            f"  [{status}] {r.node_id}{_usage_suffix(r)} · {task_preview} ({r.duration:.1f}s)"
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

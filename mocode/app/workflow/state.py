"""RunState — single source of truth for one workflow execution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .events import NodeSkippedEvent
from .graph import compute_waves, collect_ancestors

if TYPE_CHECKING:
    from .models import Node, NodeResult, Workflow


@dataclass
class RunState:
    """All mutable state for one workflow run. Created fresh per ``run()`` call."""

    context: dict = field(
        default_factory=lambda: {
            "args": {},
            "env": {},
            "nodes": {},
            "previous": "",
        }
    )
    pending_deps: dict[str, int] = field(default_factory=dict)
    activated: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    skip_recorded: set[str] = field(default_factory=set)
    skip_reasons: dict[str, str] = field(default_factory=dict)
    announced_waves: set[int] = field(default_factory=set)
    route_counter: dict[str, int] = field(default_factory=dict)
    iteration: dict[str, int] = field(default_factory=dict)
    ready_queue: list[str] = field(default_factory=list)
    total_executions: int = 0
    max_total: int = 0
    results: list[NodeResult] = field(default_factory=list)
    waves: list[list[Node]] = field(default_factory=list)
    node_wave: dict[str, int] = field(default_factory=dict)
    router_dep_extra: dict[str, int] = field(default_factory=dict)
    router_gates: dict[str, set[str]] = field(default_factory=dict)
    map_children: dict[str, list[str]] = field(default_factory=dict)  # map_id → [child_id, ...]

    # ── Factory ──────────────────────────────────────────────

    @classmethod
    def from_workflow(cls, wf: Workflow, args: dict | None = None) -> RunState:
        """Build initial state for a fresh workflow run."""
        waves = compute_waves(wf)
        node_wave = {}
        for idx, wave in enumerate(waves):
            for n in wave:
                node_wave[n.id] = idx

        state = cls(
            context={
                "args": args or {},
                "env": dict(os.environ),
                "nodes": {},
                "previous": "",
            },
            pending_deps={n.id: len(n.depends) for n in wf.nodes},
            iteration={n.id: 0 for n in wf.nodes},
            max_total=wf.max_iterations * len(wf.nodes),
            waves=waves,
            node_wave=node_wave,
        )

        # Add router gating edges — targets not in formal depends get synthetic deps
        for n in wf.nodes:
            if n.type != "router":
                continue
            gates: set[str] = set()
            ancestors = collect_ancestors(n.id, wf.node_map)
            for route in n.routes:
                for target in route.to:
                    if target in ancestors:
                        continue  # back-edge — no extra gating
                    target_node = wf.node_map.get(target)
                    if not target_node or n.id in target_node.depends:
                        continue
                    gates.add(target)
                    state.router_dep_extra[target] = (
                        state.router_dep_extra.get(target, 0) + 1
                    )
                    state.pending_deps[target] = state.pending_deps.get(target, 0) + 1
            if gates:
                state.router_gates[n.id] = gates

        # Activate root nodes
        for n in wf.root_nodes:
            state.activated.add(n.id)
            state.ready_queue.append(n.id)

        return state

    # ── Query helpers ────────────────────────────────────────

    def should_stop(self) -> bool:
        return self.total_executions >= self.max_total

    def is_ready(self, nid: str) -> bool:
        return self.pending_deps.get(nid, 0) <= 0

    # ── Mutation helpers ─────────────────────────────────────

    def activate(self, nid: str) -> None:
        """Mark a node as activated, decrement deps, optionally enqueue."""
        self.activated.add(nid)
        self.skipped.discard(nid)
        self.skip_recorded.discard(nid)
        self.pending_deps[nid] = self.pending_deps.get(nid, 0) - 1
        if self.is_ready(nid) and nid not in self.ready_queue:
            self.ready_queue.append(nid)

    def skip(self, nid: str, reason: str) -> None:
        """Record a skip (deferred — emitted on wave announce)."""
        if nid in self.skip_recorded:
            return
        self.skip_recorded.add(nid)
        self.skip_reasons[nid] = reason
        self.skipped.add(nid)

    def propagate_skip(self, nid: str, wf: Workflow) -> None:
        """Skip node and all downstream nodes not yet activated."""
        for child_id in wf.dependents.get(nid, []):
            if child_id not in self.activated and child_id not in self.skipped:
                self.skip(child_id, "dependency skipped")
                self.propagate_skip(child_id, wf)

    def flush_deferred_skips(self, wave_idx: int) -> list[NodeSkippedEvent]:
        """Emit skip events for all skipped nodes in this wave. Returns events."""
        events = []
        for n in self.waves[wave_idx]:
            if n.id in self.skip_recorded and n.id not in self.completed:
                events.append(
                    NodeSkippedEvent(
                        node_id=n.id,
                        reason=self.skip_reasons[n.id],
                        wave_idx=wave_idx,
                    )
                )
        return events

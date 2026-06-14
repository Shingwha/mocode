"""RunState — single source of truth for one workflow execution.

All mutable fields are private (``_``-prefixed). External callers
(runner, scheduler, node handlers) interact with state exclusively
through behaviour methods, which keeps cross-field invariants
encapsulated in one place.
"""

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
    """All mutable state for one workflow run. Created fresh per ``run()`` call.

    Fields are private; use the behaviour methods below. The only externally
    exposed attribute is ``skipped`` (a read-only view of the skip dict),
    preserved for compatibility with tests and direct inspection.
    """

    # ── Private storage ──────────────────────────────────────
    _context: dict = field(default_factory=dict)
    _pending_deps: dict[str, int] = field(default_factory=dict)
    _activated: set[str] = field(default_factory=set)
    _completed: set[str] = field(default_factory=set)
    _skipped: dict[str, str] = field(default_factory=dict)  # node_id → reason
    _announced_waves: set[int] = field(default_factory=set)
    _route_counter: dict[str, int] = field(default_factory=dict)
    _iteration: dict[str, int] = field(default_factory=dict)
    _ready_queue: list[str] = field(default_factory=list)
    _total_executions: int = 0
    _max_total: int = 0
    _results: list[NodeResult] = field(default_factory=list)
    _waves: list[list[Node]] = field(default_factory=list)
    _node_wave: dict[str, int] = field(default_factory=dict)
    _router_dep_extra: dict[str, int] = field(default_factory=dict)
    _router_gates: dict[str, set[str]] = field(default_factory=dict)

    # ── Factory ──────────────────────────────────────────────

    @classmethod
    def from_workflow(cls, wf: Workflow, args: dict | None = None) -> RunState:
        """Build initial state for a fresh workflow run."""
        waves = compute_waves(wf)
        node_wave = {n.id: idx for idx, wave in enumerate(waves) for n in wave}

        state = cls(
            _context={
                "args": args or {},
                "env": os.environ,
                "nodes": {},
                "previous": "",
            },
            _pending_deps={n.id: len(n.depends) for n in wf.nodes},
            _iteration={n.id: 0 for n in wf.nodes},
            _max_total=wf.max_iterations * len(wf.nodes),
            _waves=waves,
            _node_wave=node_wave,
        )

        # Router gating: targets not in formal depends get synthetic deps
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
                    state._router_dep_extra[target] = (
                        state._router_dep_extra.get(target, 0) + 1
                    )
                    state._pending_deps[target] = (
                        state._pending_deps.get(target, 0) + 1
                    )
            if gates:
                state._router_gates[n.id] = gates

        # Activate root nodes
        for n in wf.root_nodes:
            state._activated.add(n.id)
            state._ready_queue.append(n.id)

        return state

    # ── Query: stop / readiness ──────────────────────────────

    def should_stop(self) -> bool:
        return self._total_executions >= self._max_total

    def is_ready(self, nid: str) -> bool:
        return self._pending_deps.get(nid, 0) <= 0

    def has_ready(self) -> bool:
        return bool(self._ready_queue)

    def drain_ready(self) -> list[str]:
        """Return all currently-queued node ids and clear the queue."""
        batch = list(self._ready_queue)
        self._ready_queue.clear()
        return batch

    def enqueue(self, nid: str) -> None:
        """Add a node to the ready queue (idempotent)."""
        if nid not in self._ready_queue:
            self._ready_queue.append(nid)

    # ── Query: node lifecycle status ─────────────────────────

    def is_completed(self, nid: str) -> bool:
        return nid in self._completed

    def is_activated(self, nid: str) -> bool:
        return nid in self._activated

    def is_skipped(self, nid: str) -> bool:
        return nid in self._skipped

    def get_skip_reason(self, nid: str) -> str | None:
        return self._skipped.get(nid)

    @property
    def skipped(self) -> dict[str, str]:
        """Read-only-ish view of skip map (preserved for test compatibility).

        Callers may read; mutations should go through :meth:`skip`/:meth:`unskip`.
        """
        return self._skipped

    # ── Query: waves ─────────────────────────────────────────

    def wave_of(self, nid: str) -> int:
        return self._node_wave.get(nid, 0)

    def wave_count(self) -> int:
        return len(self._waves)

    def nodes_in_wave(self, idx: int) -> list[Node]:
        return self._waves[idx] if 0 <= idx < len(self._waves) else []

    def is_wave_announced(self, w: int) -> bool:
        return w in self._announced_waves

    def all_waves_announced_before(self, w: int) -> bool:
        """True iff every wave index < w has been announced."""
        return all(pw in self._announced_waves for pw in range(w))

    def announce_wave(self, w: int) -> None:
        self._announced_waves.add(w)

    def unannounce_from(self, w: int) -> None:
        """Un-announce wave w and all waves after it (for loop resets)."""
        for idx in range(w, len(self._waves)):
            self._announced_waves.discard(idx)

    # ── Query: results & context ─────────────────────────────

    def get_results(self) -> list[NodeResult]:
        return self._results

    def snapshot_results(self) -> list[NodeResult]:
        """Return a shallow copy of results (safe to persist on cancel)."""
        return list(self._results)

    def append_child_result(self, nr: NodeResult) -> None:
        """Record a map child's result without marking it a completed node.

        Children (ids like ``parent::idx``) contribute to the output history
        but are not first-class graph nodes, so they bypass context/completion.
        """
        self._results.append(nr)

    def get_context(self) -> dict:
        return self._context

    def get_node_output(self, nid: str) -> str:
        """Return the recorded output string for a completed node, or ''."""
        return self._context.get("nodes", {}).get(nid, {}).get("output", "")

    def inject(self, key: str, val) -> None:
        """Set a top-level context key (e.g. the map iteration variable)."""
        self._context[key] = val

    def withdraw(self, key: str) -> None:
        self._context.pop(key, None)

    def set_run_dir(self, run_dir: str) -> None:
        self._context["run_dir"] = run_dir

    # ── Mutation: dependency accounting ──────────────────────

    def decrement_deps(self, nid: str) -> None:
        self._pending_deps[nid] = self._pending_deps.get(nid, 0) - 1

    def reset_deps(self, nid: str, total: int) -> None:
        self._pending_deps[nid] = total

    def recompute_deps(self, nid: str, base: int, extra: int, completed_deps: int) -> None:
        """Recompute a node's pending count after a loop reset."""
        self._pending_deps[nid] = base + extra - completed_deps

    def router_extra_of(self, nid: str) -> int:
        return self._router_dep_extra.get(nid, 0)

    # ── Mutation: routing counters ───────────────────────────

    def route_count(self, key: str) -> int:
        return self._route_counter.get(key, 0)

    def increment_route(self, key: str) -> int:
        """Increment and return the new count for a route key."""
        self._route_counter[key] = self._route_counter.get(key, 0) + 1
        return self._route_counter[key]

    def router_gates_of(self, router_id: str) -> set[str]:
        return self._router_gates.get(router_id, set())

    # ── Mutation: node lifecycle ─────────────────────────────

    def activate(self, nid: str) -> None:
        """Mark a node activated: clear prior skip, decrement deps, enqueue if ready."""
        self._activated.add(nid)
        self._skipped.pop(nid, None)
        self._pending_deps[nid] = self._pending_deps.get(nid, 0) - 1
        if self.is_ready(nid):
            self.enqueue(nid)

    def deactivate(self, nid: str) -> None:
        self._activated.discard(nid)

    def add_activated(self, nid: str) -> None:
        """Mark activated without touching deps/skip (used by activate_downstream un-skip)."""
        self._activated.add(nid)

    def complete(self, nid: str) -> None:
        self._completed.add(nid)

    def uncomplete(self, nid: str) -> None:
        self._completed.discard(nid)

    def record_node_done(self, nid: str, nr: NodeResult, node_ctx: dict) -> None:
        """Append result, register context, mark completed, set ``previous``."""
        self._results.append(nr)
        self._context["nodes"][nid] = node_ctx
        self._context["previous"] = nr.output
        self._completed.add(nid)

    def increment_total(self, count: int = 1) -> None:
        self._total_executions += count

    def increment_iteration(self, nid: str) -> None:
        self._iteration[nid] = self._iteration.get(nid, 0) + 1

    def skip(self, nid: str, reason: str) -> None:
        """Record a skip (deferred — emitted on wave announce). Idempotent."""
        if nid in self._skipped:
            return
        self._skipped[nid] = reason

    def unskip(self, nid: str) -> None:
        self._skipped.pop(nid, None)

    def propagate_skip(self, nid: str, wf: Workflow) -> None:
        """Skip a node and propagate to downstream nodes not yet activated.

        A skipped dep counts as "resolved": ``pending_deps`` is decremented so
        nodes with other valid dependency paths can still become ready.
        """
        for child_id in wf.dependents.get(nid, []):
            if child_id in self._activated:
                continue
            self.decrement_deps(child_id)
            if child_id in self._skipped:
                continue
            if self.is_ready(child_id):
                # All deps resolved — don't skip, let it execute
                self.enqueue(child_id)
            else:
                self.skip(child_id, "dependency skipped")
                self.propagate_skip(child_id, wf)

    # ── Events ───────────────────────────────────────────────

    def flush_deferred_skips(self, wave_idx: int) -> list[NodeSkippedEvent]:
        """Emit skip events for all skipped nodes in this wave. Returns events."""
        events = []
        for n in self._waves[wave_idx]:
            if n.id in self._skipped and n.id not in self._completed:
                events.append(
                    NodeSkippedEvent(
                        node_id=n.id,
                        reason=self._skipped[n.id],
                        wave_idx=wave_idx,
                    )
                )
        return events

"""Scheduler — DAG scheduling logic extracted from DAGRunner.

Contains:
- announce_waves: wave announcement with deferred skip flushing
- evaluate_router: router node condition evaluation
- handle_back_edge: back-edge (loop) handling
- activate_downstream: decrement deps and enqueue ready nodes
- reset_downstream: clear completion state for loop resets
- collect_downstream: BFS collect all downstream nodes
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable

    from .events import WorkflowEvent
    from .models import Node, Workflow
    from .state import RunState

from .events import (
    LoopIterEvent,
    RouterConditionEvent,
    WaveReadyEvent,
)
from .models import NodeResult


class Scheduler:
    """DAG scheduling logic — wave announcement, router evaluation,
    back-edge handling, and downstream activation."""

    def __init__(
        self,
        workflow: Workflow,
        emit: Callable[[WorkflowEvent], None],
    ):
        self.workflow = workflow
        self._emit = emit

    # ── Wave announcement ─────────────────────────────────────

    def announce_waves(self, state: RunState) -> None:
        """Announce waves in order, only when all prior waves announced."""
        total = len(state.waves)
        for w in range(total):
            if w in state.announced_waves:
                continue
            if any(pw not in state.announced_waves for pw in range(w)):
                break
            if not all(state.is_ready(n.id) for n in state.waves[w]):
                break
            state.announced_waves.add(w)

            # Flush deferred skips for this wave
            for event in state.flush_deferred_skips(w):
                self._emit(event)

            node_ids = [
                n.id
                for n in state.waves[w]
                if n.id not in state.skipped and n.id not in state.completed
            ]
            if node_ids:
                self._emit(
                    WaveReadyEvent(
                        wave_idx=w,
                        total_waves=total,
                        node_ids=node_ids,
                    )
                )

    # ── Router evaluation ─────────────────────────────────────

    def evaluate_router(self, node: Node, state: RunState) -> None:
        """Evaluate a router node's routes and activate targets."""
        wf = self.workflow
        router_wave = state.node_wave.get(node.id, 0)

        combined = "\n".join(
            state.context.get("nodes", {}).get(dep, {}).get("output", "")
            for dep in node.depends
        )

        matched = False
        all_targets: set[str] = set()
        had_back_edge = False

        for ri, route in enumerate(node.routes):
            route_key = f"{node.id}:{ri}"
            if route.max > 0 and state.route_counter.get(route_key, 0) >= route.max:
                continue
            if route.match is not None and not re.search(route.match, combined):
                continue

            state.route_counter[route_key] = state.route_counter.get(route_key, 0) + 1
            matched = True

            self._emit(
                RouterConditionEvent(
                    router_id=node.id,
                    matched=True,
                    branch=f"route {ri}",
                    targets=route.to,
                    wave_idx=router_wave,
                )
            )

            for target_id in route.to:
                all_targets.add(target_id)
                if target_id in state.completed:
                    had_back_edge = True
                    self.handle_back_edge(node, target_id, route_key, ri, state)
                else:
                    state.activate(target_id)
            break  # first-match-wins

        if not matched:
            self._emit(
                RouterConditionEvent(
                    router_id=node.id,
                    matched=False,
                    branch="no match",
                    targets=[],
                    wave_idx=router_wave,
                )
            )

        state.completed.add(node.id)

        # Skip downstream nodes not activated by any route.
        # BUT if a back-edge fired, skip NOTHING — the router will re-evaluate
        # after the loop completes, giving un-targeted nodes another chance.
        if not had_back_edge:
            for dep_id in wf.dependents.get(node.id, []):
                if dep_id not in all_targets and dep_id not in state.activated:
                    state.pending_deps[dep_id] = state.pending_deps.get(dep_id, 0) - 1
                    state.skip(dep_id, "not activated by router")
                    state.propagate_skip(dep_id, wf)

            # Also skip router-gated targets not activated
            for target_id in state.router_gates.get(node.id, set()):
                if target_id not in all_targets and target_id not in state.activated:
                    state.pending_deps[target_id] = (
                        state.pending_deps.get(target_id, 0) - 1
                    )
                    state.skip(target_id, "not activated by router")

    # ── Back-edge handling ────────────────────────────────────

    def handle_back_edge(
        self,
        router: Node,
        target_id: str,
        route_key: str,
        route_idx: int,
        state: RunState,
    ) -> None:
        """Handle a back-edge: reset downstream, re-enqueue target, emit loop event."""
        wf = self.workflow

        # Collect and reset all downstream nodes
        downstream = self.collect_downstream(target_id, wf)
        downstream.add(target_id)
        self.reset_downstream(downstream, wf, state)

        # Re-announce waves from target's wave onward
        target_wave = state.node_wave.get(target_id)
        if target_wave is not None:
            for w in range(target_wave, len(state.waves)):
                state.announced_waves.discard(w)

        # Router must re-evaluate after back-edge
        state.completed.discard(router.id)
        state.pending_deps[router.id] = len(router.depends) - 1

        # Re-enqueue target
        state.activate(target_id)

        retry_count = state.route_counter[route_key]
        max_iter = next(
            (r.max for r in router.routes if r.max > 0),
            0,
        )
        iter_result = NodeResult(
            node_id=target_id,
            task="",
            output="",
            exit_code=0,
            duration=0,
            iteration=retry_count,
        )
        self._emit(
            LoopIterEvent(
                node_id=target_id,
                description=router.description,
                iteration=retry_count,
                max_iter=max_iter,
                result=iter_result,
            )
        )

    # ── Downstream helpers ────────────────────────────────────

    @staticmethod
    def reset_downstream(
        downstream: set[str], wf: Workflow, state: RunState
    ) -> None:
        """Reset all downstream nodes: clear completion state and recompute pending deps."""
        for nid in downstream:
            state.completed.discard(nid)
            state.skipped.pop(nid, None)
            state.activated.discard(nid)

        for nid in downstream:
            node = wf.node_map.get(nid)
            if node:
                base = len(node.depends)
                extra = state.router_dep_extra.get(nid, 0)
                state.pending_deps[nid] = base + extra
                completed_deps = sum(1 for d in node.depends if d in state.completed)
                state.pending_deps[nid] -= completed_deps

    @staticmethod
    def collect_downstream(node_id: str, wf: Workflow) -> set[str]:
        """BFS collect all nodes downstream of given node."""
        visited: set[str] = set()
        stack = list(wf.dependents.get(node_id, []))
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            stack.extend(wf.dependents.get(nid, []))
        return visited

    @staticmethod
    def activate_downstream(node_id: str, wf: Workflow, state: RunState) -> None:
        """Decrement pending_deps for dependents and enqueue ready ones."""
        for dep_id in wf.dependents.get(node_id, []):
            if dep_id in state.skipped:
                continue
            state.pending_deps[dep_id] = state.pending_deps.get(dep_id, 0) - 1
            if state.is_ready(dep_id) and dep_id not in state.ready_queue:
                state.ready_queue.append(dep_id)

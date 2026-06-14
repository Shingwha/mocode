"""Scheduler — DAG-level scheduling primitives.

Pure graph/topology operations shared by the runner and the router handler:

- :meth:`announce_waves` — wave announcement with deferred skip flushing
- :meth:`reset_for_loop` — back-edge (loop) reset: clear downstream state and
  re-arm the router so it can re-evaluate after the loop body completes
- :meth:`activate_downstream` — decrement deps and enqueue ready nodes
- :meth:`reset_downstream` — clear completion state for loop resets
- :meth:`collect_downstream` — BFS collect all downstream nodes

Route *evaluation* (matching + target activation + untargeted skipping) has
moved to :class:`RouterNodeHandler`; this module no longer owns it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable

    from .events import WorkflowEvent
    from .models import Node, Workflow
    from .state import RunState

from .events import LoopIterEvent
from .models import NodeResult


class Scheduler:
    """DAG scheduling primitives — wave announcement, downstream activation,
    loop resets. Stateless aside from the workflow reference and emit sink."""

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
        total = state.wave_count()
        for w in range(total):
            if state.is_wave_announced(w):
                continue
            if not state.all_waves_announced_before(w):
                break
            if not all(state.is_ready(n.id) for n in state.nodes_in_wave(w)):
                break
            state.announce_wave(w)

            # Flush deferred skips for this wave.
            for event in state.flush_deferred_skips(w):
                self._emit(event)

            node_ids = [
                n.id
                for n in state.nodes_in_wave(w)
                if not state.is_skipped(n.id) and not state.is_completed(n.id)
            ]
            if node_ids:
                from .events import WaveReadyEvent

                self._emit(
                    WaveReadyEvent(
                        wave_idx=w,
                        total_waves=total,
                        node_ids=node_ids,
                    )
                )

    # ── Back-edge (loop) reset ────────────────────────────────

    def reset_for_loop(
        self,
        router: Node,
        target_id: str,
        route_key: str,
        route_idx: int,
        state: RunState,
    ) -> None:
        """Reset downstream of ``target_id``, re-enqueue it, emit a loop event.

        Called by :class:`RouterNodeHandler` when a route targets an
        already-completed node (back-edge). The router is un-completed so it
        will re-evaluate once the loop body finishes.
        """
        wf = self.workflow

        # Collect and reset all downstream nodes (including the target).
        downstream = self.collect_downstream(target_id, wf)
        downstream.add(target_id)
        self.reset_downstream(downstream, wf, state)

        # Re-announce waves from the target's wave onward.
        target_wave = state.wave_of(target_id)
        state.unannounce_from(target_wave)

        # Router must re-evaluate after the back-edge.
        state.uncomplete(router.id)
        # One of the router's deps (the loop body) is about to re-run, so the
        # router has len(depends)-1 currently-satisfied deps.
        state.reset_deps(router.id, len(router.depends) - 1)

        # Re-enqueue the loop target.
        state.activate(target_id)

        retry_count = state.route_count(route_key)
        max_iter = next(
            (r.max for r in router.routes if r.max > 0),
            0,
        )
        iter_result = NodeResult(
            node_id=target_id, task="", output="", exit_code=0, duration=0,
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
        """Reset nodes: clear completion/activation state, recompute pending deps."""
        for nid in downstream:
            state.uncomplete(nid)
            state.unskip(nid)
            state.deactivate(nid)

        for nid in downstream:
            node = wf.node_map.get(nid)
            if node:
                base = len(node.depends)
                extra = state.router_extra_of(nid)
                completed_deps = sum(
                    1 for d in node.depends if state.is_completed(d)
                )
                state.recompute_deps(nid, base, extra, completed_deps)

    @staticmethod
    def collect_downstream(node_id: str, wf: Workflow) -> set[str]:
        """BFS collect all nodes downstream of the given node."""
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
        """Decrement pending_deps for dependents and enqueue ready ones.

        If a dependent was previously skipped (e.g. by a router) but all its
        remaining dependencies have now resolved, un-skip it so it can execute.
        """
        for dep_id in wf.dependents.get(node_id, []):
            state.decrement_deps(dep_id)
            if state.is_skipped(dep_id):
                # Re-evaluate: maybe all other deps are now satisfied.
                if state.is_ready(dep_id):
                    state.unskip(dep_id)
                    state.add_activated(dep_id)
                    state.enqueue(dep_id)
                continue
            if state.is_ready(dep_id):
                state.enqueue(dep_id)

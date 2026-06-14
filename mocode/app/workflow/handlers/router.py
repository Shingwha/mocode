"""RouterNodeHandler — evaluates ``router`` node conditions and activates targets.

Absorbs the responsibilities of the former ``Scheduler.evaluate_router`` and
``Scheduler.handle_back_edge``:

- concatenates dependency outputs and tests each route's regex (first match wins)
- activates route targets, or dispatches a back-edge loop reset for already-completed targets
- after a non-back-edge decision, skips downstream nodes the router did not target

The handler reports completion via ``ctx.on_complete`` (like task/map handlers)
so the runner's downstream-activation bookkeeping runs uniformly. ``reset_for_loop``
is invoked for back-edges.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..events import RouterConditionEvent

if TYPE_CHECKING:
    from ..models import Node, Route
    from .base import NodeExecContext


class RouterNodeHandler:
    """Synchronous handler: evaluates routes, never runs an AgentLoop."""

    type_name = "router"
    synchronous = True

    # ── NodeHandler protocol ─────────────────────────────────

    async def execute(self, node: Node, ctx: NodeExecContext) -> None:
        """Evaluate routes, activate targets or loop back, then mark router done.

        Routers own their full lifecycle: they do NOT call ``ctx.on_complete``
        (unlike task/map handlers) because they produce no ``NodeResult`` and
        their targets are activated directly via ``state.activate`` rather than
        through the runner's downstream-activation path.
        """
        state = ctx.state
        router_wave = state.wave_of(node.id)

        combined = "\n".join(state.get_node_output(dep) for dep in node.depends)

        matched, all_targets, had_back_edge = self._match_and_activate(
            node, combined, ctx
        )

        if not matched:
            ctx.emit(
                RouterConditionEvent(
                    router_id=node.id,
                    matched=False,
                    branch="no match",
                    targets=[],
                    wave_idx=router_wave,
                )
            )

        # Router is done once evaluated.
        state.complete(node.id)

        # Skip downstream nodes not activated by any route — UNLESS a back-edge
        # fired: the router will re-evaluate after the loop, giving un-targeted
        # nodes another chance.
        if not had_back_edge:
            self._skip_untargeted(node, all_targets, ctx)

    # ── Route matching & target activation ───────────────────

    def _match_and_activate(
        self,
        node: Node,
        combined: str,
        ctx: NodeExecContext,
    ) -> tuple[bool, set[str], bool]:
        """Try routes in order; first match wins. Returns (matched, targets, had_back_edge)."""
        state = ctx.state
        router_wave = state.wave_of(node.id)
        all_targets: set[str] = set()
        had_back_edge = False

        for ri, route in enumerate(node.routes):
            route_key = f"{node.id}:{ri}"
            if route.max > 0 and state.route_count(route_key) >= route.max:
                continue
            if route.match is not None and not re.search(route.match, combined):
                continue

            state.increment_route(route_key)
            ctx.emit(
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
                if state.is_completed(target_id):
                    # Back-edge: target already ran — reset and loop back.
                    had_back_edge = True
                    ctx.reset_for_loop(node, target_id, route_key, ri)
                else:
                    state.activate(target_id)
            return True, all_targets, had_back_edge

        return False, all_targets, had_back_edge

    # ── Untargeted downstream skipping ───────────────────────

    def _skip_untargeted(
        self,
        node: Node,
        all_targets: set[str],
        ctx: NodeExecContext,
    ) -> None:
        """Skip dependents and router-gated targets not reached by any route."""
        state = ctx.state
        wf = ctx.workflow

        for dep_id in wf.dependents.get(node.id, []):
            if dep_id in all_targets or state.is_activated(dep_id):
                continue
            state.decrement_deps(dep_id)
            state.skip(dep_id, "not activated by router")
            state.propagate_skip(dep_id, wf)

        for target_id in state.router_gates_of(node.id):
            if target_id in all_targets or state.is_activated(target_id):
                continue
            state.decrement_deps(target_id)
            state.skip(target_id, "not activated by router")

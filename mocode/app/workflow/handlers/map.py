"""MapNodeHandler — fans a ``map`` node out into N concurrent child tasks.

Absorbs the fan-out responsibilities of the former ``DAGRunner._fan_out_children``
and ``Executor._finalize_map`` / ``_finalize_empty_map``:

- resolves ``node.each`` to a list of items
- spawns one AgentLoop per child (concurrent, each owns its own history)
- emits ``MapFanOutEvent`` / ``MapItemDoneEvent``
- aggregates child outputs and usage into a single parent ``NodeResult``
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..events import MapFanOutEvent, MapItemDoneEvent, NodeStartEvent
from ..models import NodeResult, fill_template, resolve_list_expr

if TYPE_CHECKING:
    from ..models import Node
    from .base import NodeExecContext


class MapNodeHandler:
    """Runs ``map`` nodes by fanning out to N concurrent child tasks.

    Children execute through ``ctx.exec_node`` (the runner's ``_exec_node``
    seam), so this handler owns only fan-out coordination and result
    aggregation — not AgentLoop management.
    """

    type_name = "map"
    synchronous = False

    def __init__(self, *, timeout: int) -> None:
        self.timeout = timeout

    # ── NodeHandler protocol ─────────────────────────────────

    async def execute(self, node: Node, ctx: NodeExecContext) -> None:
        """Resolve items, fan out children concurrently, aggregate, report done."""
        items = resolve_list_expr(node.each, ctx.state.get_context())

        if not items:
            # Empty fan-out: produce an empty result without spawning children.
            empty_result = NodeResult(
                node_id=node.id, task=node.task, output="",
                exit_code=0, duration=0,
            )
            ctx.state.increment_total()
            ctx.on_complete(node, empty_result, None)
            return

        wave_idx = ctx.state.wave_of(node.id)
        context_header = (
            self._build_context_header(node, ctx)
            if ctx.node_context_enabled
            else None
        )

        async with ctx.semaphore:
            ctx.emit(NodeStartEvent(node_id=node.id, description=node.description))
            ctx.emit(
                MapFanOutEvent(
                    map_id=node.id, item_count=len(items), wave_idx=wave_idx
                )
            )
            child_results = await self._run_children(
                node, items, context_header, ctx
            )

        # Per-item completion events + record each child in the result history.
        for idx, nr in enumerate(child_results):
            ctx.state.append_child_result(nr)
            ctx.emit(
                MapItemDoneEvent(
                    map_id=node.id,
                    item_index=idx,
                    total_count=len(items),
                    item_value=items[idx][:80],
                    duration=nr.duration,
                    tool_calls=nr.tool_calls,
                    prompt_tokens=nr.prompt_tokens,
                    completion_tokens=nr.completion_tokens,
                )
            )

        # Aggregate child outputs/usage into one parent result.
        total_duration = sum(nr.duration for nr in child_results)
        merged_output = "\n---\n".join(nr.output for nr in child_results)
        map_result = NodeResult(
            node_id=node.id,
            task=node.task,
            output=merged_output,
            exit_code=0,
            duration=total_duration,
            tool_calls=sum(nr.tool_calls for nr in child_results),
            prompt_tokens=sum(nr.prompt_tokens for nr in child_results),
            completion_tokens=sum(nr.completion_tokens for nr in child_results),
        )

        # Count children + parent as executions.
        ctx.state.increment_total(len(child_results) + 1)
        ctx.on_complete(
            node, map_result,
            f"Map '{node.id}' done — {len(items)} items ({total_duration:.1f}s)",
        )

    # ── Child execution ──────────────────────────────────────

    async def _run_children(
        self,
        node: Node,
        items: list[str],
        context_header: str | None,
        ctx: NodeExecContext,
    ) -> list[NodeResult]:
        """Spawn one child per item concurrently and return their results.

        Each child gets its own context with the iteration variable injected,
        so templates can reference ``{node.as_}``. Children run through
        ``ctx.exec_node`` (the runner's ``_exec_node`` seam) so that
        ``patch.object(runner, "_exec_node", ...)`` intercepts them too.
        """
        child_tasks: list[asyncio.Task[NodeResult]] = []
        for idx, item_val in enumerate(items):
            child_id = f"{node.id}::{idx}"
            ctx.state.inject(node.as_, item_val)
            child_task_text = fill_template(node.task, ctx.state.get_context())
            child_tasks.append(
                asyncio.create_task(
                    ctx.exec_node(child_id, child_task_text, context_header)
                )
            )
        # Clean up the last injected iteration variable.
        ctx.state.withdraw(node.as_)
        return list(await asyncio.gather(*child_tasks))

    # ── Context header (mirrors TaskNodeHandler) ─────────────

    @staticmethod
    def _build_context_header(node: Node, ctx: NodeExecContext) -> str:
        from .task import TaskNodeHandler

        return TaskNodeHandler.build_context_header(node, ctx.workflow, ctx.run_dir)

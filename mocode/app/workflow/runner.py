"""DAGRunner — async event-driven execution engine for Workflow DAGs.

Each task node runs as an in-process AgentLoop (no subprocess).
Router nodes evaluate conditions. Back-edges from routers enable loops.
Nodes execute serially (max_concurrency=1) to avoid overloading LLM APIs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .models import Node, NodeResult, Workflow
    from .run_store import WorkflowRunStore

    from ..core.agent import AgentLoop

from .events import (
    MapFanOutEvent,
    MapItemDoneEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    ProgressEvent,
    WorkflowEvent,
)
from .executor import Executor
from .hooks import _WorkflowNodeHook  # noqa: F401 — re-export for backward compat
from .models import fill_template, parse_items, parse_sections
from .scheduler import Scheduler  # noqa: F401 — re-export for backward compat
from .state import RunState


class DAGRunner:
    """Executes a Workflow DAG by running each task node as an in-process AgentLoop.

    Supports three node types: task, router, and map.
    Map nodes fan out to N child tasks and run them concurrently.
    ``concurrency`` controls max parallel execution (default 1 = serial).
    """

    def __init__(
        self,
        workflow: Workflow,
        parent_agent: AgentLoop,
        timeout: int = 300,
        node_context: bool = True,
        on_event: Callable[[WorkflowEvent], None] | None = None,
        run_id: str | None = None,
        run_store: WorkflowRunStore | None = None,
    ):
        self.workflow = workflow
        self._parent_agent = parent_agent
        self.timeout = timeout
        self.node_context = node_context
        self._on_event = on_event
        self.run_id = run_id
        self.run_store = run_store
        self._semaphore = asyncio.Semaphore(workflow.concurrency)
        self.partial_results: list[NodeResult] = []  # populated on cancellation
        self._executor = Executor(
            parent_agent,
            timeout=timeout,
            on_event=on_event,
        )
        self._scheduler = Scheduler(
            workflow,
            emit=on_event or (lambda e: None),
        )

    # ── Event dispatch ────────────────────────────────────────

    def _emit(self, event: WorkflowEvent) -> None:
        if self._on_event:
            self._on_event(event)

    # ── Persistence ─────────────────────────────────────────────

    def _persist(self, results: list[NodeResult], status: str) -> None:
        """Write results to run_store if configured."""
        if self.run_store and self.run_id:
            finished = datetime.now().isoformat() if status != "running" else None
            self.run_store.update(self.run_id, results, status, finished)

    # ── Main execution loop ───────────────────────────────────

    async def run(self, args: dict | None = None) -> list[NodeResult]:
        """Execute the full workflow DAG. Returns all NodeResults."""
        wf = self.workflow
        state = RunState.from_workflow(wf, args)

        try:
            while state.ready_queue and not state.should_stop():
                self._scheduler.announce_waves(state)

                # Collect all ready nodes at once for potential parallelism.
                # Filter out any nodes that were queued then skipped by
                # propagate_skip before we got to process them.
                batch = [nid for nid in state.ready_queue if nid not in state.skipped]
                state.ready_queue.clear()

                # Run the batch concurrently
                tasks = []
                for nid in batch:
                    node = wf.node_map[nid]
                    state.iteration[nid] += 1

                    if node.type == "router":
                        # Routers are sync — run inline, add to batch of 1
                        self._scheduler.evaluate_router(node, state)
                    else:
                        tasks.append(self._run_task_node(node, state))

                if tasks:
                    await asyncio.gather(*tasks)

                if state.should_stop():
                    break

            # Remaining unactivated nodes → skipped
            for n in wf.nodes:
                if n.id not in state.completed and n.id not in state.skipped:
                    state.skip(n.id, "not activated")
                    wave = state.node_wave.get(n.id, 0)
                    self._emit(
                        NodeSkippedEvent(
                            node_id=n.id,
                            reason="not activated",
                            wave_idx=wave,
                        )
                    )

            self._persist(state.results, "completed")
            return state.results

        except asyncio.CancelledError:
            self.partial_results = list(state.results)
            self._persist(state.results, "cancelled")
            raise
        except Exception:
            self._persist(state.results, "failed")
            raise

    # ── Delegated node execution (to Executor) ───────────────

    async def _run_task_node(self, node: Node, state: RunState) -> None:
        """Fill template, execute via AgentLoop, record result.

        If ``node.each`` is set, fan-out to N child tasks instead of single execution.
        """
        if node.each:
            items = _resolve_items(node.each, state.context)
            if not items:
                self._executor._finalize_empty_map(node, state, self._record_node_done)
                return
            async with self._semaphore:
                self._emit(NodeStartEvent(node_id=node.id, description=node.description))
            child_results = await self._fan_out_children(node, items, state)
            self._executor._finalize_map(
                node, items, child_results, state, self._record_node_done
            )
            return

        wf = self.workflow
        task_text = fill_template(node.task, state.context)

        context_header = (
            self._executor._build_node_context_header(node, wf)
            if self.node_context else None
        )
        async with self._semaphore:
            self._emit(NodeStartEvent(node_id=node.id, description=node.description))
            nr = await self._exec_node(node.id, task_text, context_header)
        state.total_executions += 1

        self._record_node_done(
            node,
            nr,
            state,
            progress_message=f"Node '{node.id}' done ({nr.duration:.1f}s)",
        )

    async def _fan_out_children(
        self, node: Node, items: list[str], state: RunState
    ) -> list[NodeResult]:
        """Create and run child tasks concurrently, return their results."""
        wf = self.workflow
        wave_idx = state.node_wave.get(node.id, 0)
        self._emit(
            MapFanOutEvent(map_id=node.id, item_count=len(items), wave_idx=wave_idx)
        )

        # Build context header once (same for all children)
        context_header = (
            self._executor._build_node_context_header(node, wf)
            if self.node_context else None
        )

        child_ids: list[str] = []
        child_tasks: list[asyncio.Task[NodeResult]] = []
        for idx, item_val in enumerate(items):
            child_id = f"{node.id}::{idx}"
            child_ids.append(child_id)
            # Inject item into context top level, fill_template handles {as_}
            state.context[node.as_] = item_val
            child_task_text = fill_template(node.task, state.context)
            child_tasks.append(
                asyncio.create_task(
                    self._run_map_child(child_id, child_task_text, context_header)
                )
            )

        # Clean up injected item
        state.context.pop(node.as_, None)

        state.map_children[node.id] = child_ids
        child_results: list[NodeResult] = list(await asyncio.gather(*child_tasks))

        # Emit per-item events
        for idx, nr in enumerate(child_results):
            self._emit(
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

        return child_results

    async def _run_map_child(
        self, child_id: str, task: str, context_header: str | None
    ) -> NodeResult:
        """Run a single map child task under the semaphore."""
        async with self._semaphore:
            return await self._exec_node(child_id, task, context_header)

    async def _exec_node(
        self,
        node_id: str,
        task: str,
        context_header: str | None = None,
    ) -> NodeResult:
        """Delegate node execution to the Executor.

        Kept as a thin wrapper so that ``patch.object(runner, "_exec_node")``
        in tests continues to work without modification.
        """
        return await self._executor._exec_node(node_id, task, context_header)

    # ── Node completion helper ────────────────────────────────

    def _record_node_done(
        self,
        node: Node,
        nr: NodeResult,
        state: RunState,
        *,
        progress_message: str | None = None,
    ) -> None:
        """Record node completion: update state, persist, emit events, activate downstream.

        Centralises the ~15-line sequence duplicated in _run_task_node,
        _finalize_empty_map, and _finalize_map.
        """
        wf = self.workflow
        state.results.append(nr)
        node_ctx: dict = {
            "output": nr.output,
            "exit_code": nr.exit_code,
            "duration": nr.duration,
            "error": nr.error or "",
        }
        # Register [TAG] sections as top-level keys in node context
        for tag, values in nr.sections.items():
            node_ctx[tag] = values
        state.context["nodes"][node.id] = node_ctx
        state.context["previous"] = nr.output
        state.completed.add(node.id)
        self._persist(state.results, "running")

        wave_idx = state.node_wave.get(node.id, 0)
        self._emit(
            NodeDoneEvent(
                node_id=node.id,
                description=node.description,
                result=nr,
                wave_idx=wave_idx,
            )
        )
        if progress_message is not None:
            self._emit(
                ProgressEvent(
                    message=progress_message,
                    node_id=node.id,
                    detail=f"done ({nr.duration:.1f}s)",
                )
            )
        Scheduler.activate_downstream(node.id, wf, state)


def _resolve_items(each_expr: str, context: dict) -> list[str]:
    """Resolve ``each`` expression to a list of strings.

    Priority:
    1. Direct list resolution (e.g. ``{nodes.scan.ISSUE}`` → already a list)
    2. ``fill_template`` + line splitting (covers string outputs and mixed templates)
    """
    expr = each_expr.strip()
    if expr.startswith("{") and expr.endswith("}"):
        inner = expr[1:-1]
        parts = inner.split(".", 1)
        if len(parts) > 1:
            bucket, rest = parts
            from .models import _BUCKET_ALIASES, _resolve_path
            obj = context.get(bucket) or context.get(_BUCKET_ALIASES.get(bucket, ""))
            if isinstance(obj, dict):
                val = _resolve_path(rest, obj)
                if isinstance(val, list):
                    return [str(v) for v in val]
        else:
            # Single-segment placeholder
            val = context.get(inner)
            if isinstance(val, list):
                return [str(v) for v in val]
    # Fallback: fill template + split by lines
    filled = fill_template(each_expr, context)
    return [line.strip() for line in filled.splitlines() if line.strip()]


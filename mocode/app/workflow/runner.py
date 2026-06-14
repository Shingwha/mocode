"""DAGRunner — async event-driven execution engine for Workflow DAGs.

Node execution is delegated to per-type :class:`NodeHandler` instances looked
up from a registry, so adding a new node type no longer touches this module.
The runner owns: the main loop, fan-out coordination, result recording,
event emission, and persistence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .handlers import NodeHandlerRegistry
    from .models import Node, NodeResult, Workflow
    from .run_store import WorkflowRunStore

    from ..core.agent import AgentLoop

from .events import (
    NodeDoneEvent,
    NodeSkippedEvent,
    ProgressEvent,
    WorkflowEvent,
)
from .handlers import default_registry
from .handlers.base import NodeExecContext
from .hooks import _WorkflowNodeHook  # noqa: F401 — re-exported for compatibility
from .models import NodeResult
from .scheduler import Scheduler
from .state import RunState


class DAGRunner:
    """Executes a Workflow DAG by dispatching each node to its registered handler.

    Built-in node types: ``task`` (single AgentLoop), ``map`` (fan-out to N
    children), ``router`` (condition evaluation + back-edge loops). New types
    register their handler in the registry without modifying this class.
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
        # Ensure run directory exists and cache path
        self._run_dir: str | None = None
        if self.run_store and self.run_id:
            rd = self.run_store.ensure_run_dir(self.run_id)
            self._run_dir = str(rd)
        self._semaphore = asyncio.Semaphore(workflow.concurrency)
        self.partial_results: list[NodeResult] = []  # populated on cancellation

        # Handler registry + task handler (for _exec_node test seam).
        self._handlers: NodeHandlerRegistry = default_registry(
            parent_agent, timeout=timeout
        )
        self._task_handler = self._handlers.get("task")
        self._scheduler = Scheduler(
            workflow,
            emit=on_event or (lambda e: None),
        )

    # ── Event dispatch ────────────────────────────────────────

    def _emit(self, event: WorkflowEvent) -> None:
        if self._on_event:
            self._on_event(event)

    # ── Persistence ───────────────────────────────────────────

    def _persist(self, results: list[NodeResult], status: str) -> None:
        """Write results to run_store if configured."""
        if self.run_store and self.run_id:
            finished = datetime.now().isoformat() if status != "running" else None
            self.run_store.update(self.run_id, results, status, finished)

    # ── Single-node execution seam (test contract) ───────────

    async def _exec_node(
        self,
        node_id: str,
        task: str,
        context_header: str | None = None,
    ) -> NodeResult:
        """Delegate single-node execution to the task handler.

        Kept as a thin wrapper so ``patch.object(runner, "_exec_node", ...)``
        in tests continues to intercept node execution without modification.
        """
        return await self._task_handler.execute_node(
            node_id, task, context_header, emit=self._on_event
        )

    # ── Main execution loop ───────────────────────────────────

    async def run(self, args: dict | None = None) -> list[NodeResult]:
        """Execute the full workflow DAG. Returns all NodeResults."""
        wf = self.workflow
        state = RunState.from_workflow(wf, args)
        if self._run_dir:
            state.set_run_dir(self._run_dir)

        try:
            while state.has_ready() and not state.should_stop():
                self._scheduler.announce_waves(state)

                # Collect all ready nodes at once for potential parallelism.
                # Filter out nodes queued then skipped before we process them.
                batch = [
                    nid for nid in state.drain_ready() if not state.is_skipped(nid)
                ]

                # Partition into synchronous (router) and asynchronous handlers.
                sync_nodes: list[Node] = []
                async_nodes: list[Node] = []
                for nid in batch:
                    node = wf.node_map[nid]
                    state.increment_iteration(nid)
                    handler = self._handlers.get(node.type)
                    if getattr(handler, "synchronous", False):
                        sync_nodes.append(node)
                    else:
                        async_nodes.append(node)

                exec_ctx = self._make_exec_ctx(state)

                # Routers first (they decide which downstream nodes activate).
                for node in sync_nodes:
                    await self._handlers.get(node.type).execute(node, exec_ctx)

                # Then run task/map nodes concurrently.
                if async_nodes:
                    await asyncio.gather(
                        *(
                            self._handlers.get(node.type).execute(node, exec_ctx)
                            for node in async_nodes
                        )
                    )

                if state.should_stop():
                    break

            # Remaining unactivated nodes → skipped
            for n in wf.nodes:
                if not state.is_completed(n.id) and not state.is_skipped(n.id):
                    state.skip(n.id, "not activated")
                    wave = state.wave_of(n.id)
                    self._emit(
                        NodeSkippedEvent(
                            node_id=n.id,
                            reason="not activated",
                            wave_idx=wave,
                        )
                    )

            self._persist(state.get_results(), "completed")
            return state.get_results()

        except asyncio.CancelledError:
            self.partial_results = state.snapshot_results()
            self._persist(state.get_results(), "cancelled")
            raise
        except Exception:
            self._persist(state.get_results(), "failed")
            raise

    # ── Execution context factory ─────────────────────────────

    def _make_exec_ctx(self, state: RunState) -> NodeExecContext:
        """Build the handler context for the current run.

        ``on_complete`` and ``reset_for_loop`` are closures bound to this run's
        state, so handlers can report completion or trigger a loop reset
        without the runner passing state around explicitly.
        """
        wf = self.workflow

        def on_complete(
            node: Node, nr: NodeResult, progress_message: str | None
        ) -> None:
            """Record node completion: update state, persist, emit, activate downstream."""
            node_ctx: dict = {
                "output": nr.output,
                "exit_code": nr.exit_code,
                "duration": nr.duration,
                "error": nr.error or "",
            }
            for tag, values in nr.sections.items():
                node_ctx[tag] = values
            state.record_node_done(node.id, nr, node_ctx)
            self._persist(state.get_results(), "running")

            wave_idx = state.wave_of(node.id)
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

        def reset_for_loop(
            router: Node, target_id: str, route_key: str, route_idx: int
        ) -> None:
            """Back-edge callback: delegate to Scheduler.reset_for_loop."""
            self._scheduler.reset_for_loop(
                router, target_id, route_key, route_idx, state
            )

        async def exec_node(
            node_id: str, task: str, context_header: str | None = None
        ) -> NodeResult:
            """Single-node execution seam bound to this runner.

            Handlers MUST run nodes through this rather than calling their
            own execute method directly, so that
            ``patch.object(runner, "_exec_node", ...)`` in tests intercepts
            all node execution (including map children).
            """
            return await self._exec_node(node_id, task, context_header)

        return NodeExecContext(
            workflow=wf,
            state=state,
            emit=self._emit,
            semaphore=self._semaphore,
            run_dir=self._run_dir,
            node_context_enabled=self.node_context,
            on_complete=on_complete,
            reset_for_loop=reset_for_loop,
            exec_node=exec_node,
        )

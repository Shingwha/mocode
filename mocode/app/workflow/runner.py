"""DAGRunner — async event-driven execution engine for Workflow DAGs.

Each task node spawns a ``mocode -p`` subprocess. Router nodes evaluate
conditions. Back-edges from routers enable loops. Nodes execute serially
(max_concurrency=1) to avoid overloading LLM APIs.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .models import Node, NodeResult, Workflow
    from .run_store import WorkflowRunStore

from .events import (
    LoopIterEvent,
    MapFanOutEvent,
    MapItemDoneEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
    WorkflowEvent,
)
from .models import NodeResult, fill_template, parse_items
from .state import RunState


class DAGRunner:
    """Executes a Workflow DAG by spawning mocode -p for each task node.

    Supports three node types: task, router, and map.
    Map nodes fan out to N child tasks and run them concurrently.
    ``concurrency`` controls max parallel execution (default 1 = serial).
    """

    def __init__(
        self,
        workflow: Workflow,
        mocode_cmd: str = "mocode",
        timeout: int = 300,
        node_context: bool = True,
        on_event: Callable[[WorkflowEvent], None] | None = None,
        run_id: str | None = None,
        run_store: WorkflowRunStore | None = None,
    ):
        self.workflow = workflow
        self.mocode_cmd = mocode_cmd
        self.timeout = timeout
        self.node_context = node_context
        self._on_event = on_event
        self.run_id = run_id
        self.run_store = run_store
        self._semaphore = asyncio.Semaphore(workflow.concurrency)

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
                self._announce_waves(state)

                # Collect all ready nodes at once for potential parallelism
                batch = list(state.ready_queue)
                state.ready_queue.clear()

                # Run the batch concurrently
                tasks = []
                for nid in batch:
                    node = wf.node_map[nid]
                    state.iteration[nid] += 1

                    if node.type == "router":
                        # Routers are sync — run inline, add to batch of 1
                        self._evaluate_router(node, state)
                    elif node.type == "map":
                        tasks.append(self._run_map_node(node, state))
                    else:
                        tasks.append(self._run_task_node(node, state))

                if tasks:
                    await asyncio.gather(*tasks)

                if state.should_stop():
                    break

            # Remaining unactivated nodes → skipped
            for n in wf.nodes:
                if n.id not in state.completed and n.id not in state.skip_recorded:
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

        except Exception:
            self._persist(state.results, "failed")
            raise

    # ── Wave announcement ─────────────────────────────────────

    def _announce_waves(self, state: RunState) -> None:
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
                if n.id not in state.skip_recorded and n.id not in state.completed
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

    def _evaluate_router(self, node: Node, state: RunState) -> None:
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
                    self._handle_back_edge(node, target_id, route_key, ri, state)
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

    def _handle_back_edge(
        self,
        router: Node,
        target_id: str,
        route_key: str,
        route_idx: int,
        state: RunState,
    ) -> None:
        """Handle a back-edge: reset downstream, re-enqueue target, emit loop event."""
        wf = self.workflow

        # Collect all nodes downstream of target (BFS)
        downstream = self._collect_downstream(target_id, wf)
        downstream.add(target_id)

        # Reset all of them — two passes to avoid order-dependent pending_deps
        for nid in downstream:
            state.completed.discard(nid)
            state.skipped.discard(nid)
            state.skip_recorded.discard(nid)
            state.activated.discard(nid)

        for nid in downstream:
            node = wf.node_map.get(nid)
            if node:
                base = len(node.depends)
                extra = state.router_dep_extra.get(nid, 0)
                state.pending_deps[nid] = base + extra
                completed_deps = sum(1 for d in node.depends if d in state.completed)
                state.pending_deps[nid] -= completed_deps

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

    @staticmethod
    def _collect_downstream(node_id: str, wf: Workflow) -> set[str]:
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

    # ── Task node execution ───────────────────────────────────

    async def _run_task_node(self, node: Node, state: RunState) -> None:
        """Fill template, execute via subprocess, record result."""
        wf = self.workflow
        task_text = fill_template(node.task, state.context)
        self._emit(NodeStartEvent(node_id=node.id, description=node.description))

        context_header = (
            self._build_node_context_header(node, wf) if self.node_context else None
        )
        async with self._semaphore:
            nr = await self._exec_node(node.id, task_text, context_header)
        state.total_executions += 1

        state.results.append(nr)
        state.context["nodes"][node.id] = {
            "output": nr.output,
            "exit_code": nr.exit_code,
            "duration": nr.duration,
            "error": nr.error or "",
        }
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
        self._emit(ProgressEvent(message=f"Node '{node.id}' done ({nr.duration:.1f}s)"))

        # Activate downstream dependents
        self._activate_downstream(node.id, wf, state)

    # ── Map node execution ────────────────────────────────────

    async def _run_map_node(self, node: Node, state: RunState) -> None:
        """Fan-out: parse items, run a child task for each, concatenate results."""
        self._emit(NodeStartEvent(node_id=node.id, description=node.description))

        items_raw = fill_template(node.items, state.context)
        items = parse_items(items_raw)

        if not items:
            self._finalize_empty_map(node, state)
            return

        child_results = await self._fan_out_children(node, items, state)
        self._finalize_map(node, items, child_results, state)

    def _finalize_empty_map(self, node: Node, state: RunState) -> None:
        """Handle a map node with no items — produce empty output."""
        wf = self.workflow
        empty_result = NodeResult(
            node_id=node.id,
            task=node.task,
            output="",
            exit_code=0,
            duration=0,
        )
        state.results.append(empty_result)
        state.total_executions += 1
        state.context["nodes"][node.id] = {
            "output": "",
            "exit_code": 0,
            "duration": 0,
            "error": "",
        }
        state.context["previous"] = ""
        state.completed.add(node.id)
        self._emit(
            NodeDoneEvent(
                node_id=node.id,
                description=node.description,
                result=empty_result,
                wave_idx=state.node_wave.get(node.id, 0),
            )
        )
        self._activate_downstream(node.id, wf, state)

    async def _fan_out_children(
        self, node: Node, items: list[str], state: RunState
    ) -> list[NodeResult]:
        """Create and run child tasks concurrently, return their results."""
        wf = self.workflow
        wave_idx = state.node_wave.get(node.id, 0)
        self._emit(
            MapFanOutEvent(map_id=node.id, item_count=len(items), wave_idx=wave_idx)
        )

        child_ids: list[str] = []
        child_tasks: list[asyncio.Task[NodeResult]] = []
        for idx, item_val in enumerate(items):
            child_id = f"{node.id}::{idx}"
            child_ids.append(child_id)
            child_task_text = node.task.replace(f"{{{{{node.item_key}}}}}", item_val)
            context_header = (
                self._build_node_context_header(node, wf) if self.node_context else None
            )
            child_tasks.append(
                asyncio.create_task(
                    self._run_map_child(child_id, child_task_text, context_header)
                )
            )

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
                )
            )

        return child_results

    def _finalize_map(
        self,
        node: Node,
        items: list[str],
        child_results: list[NodeResult],
        state: RunState,
    ) -> None:
        """Merge child outputs, record result, activate downstream."""
        wf = self.workflow
        total_duration = sum(nr.duration for nr in child_results)
        merged_output = "\n---\n".join(nr.output for nr in child_results)

        for nr in child_results:
            state.total_executions += 1
            state.results.append(nr)

        map_result = NodeResult(
            node_id=node.id,
            task=node.task,
            output=merged_output,
            exit_code=0,
            duration=total_duration,
        )
        state.results.append(map_result)
        state.total_executions += 1

        state.context["nodes"][node.id] = {
            "output": merged_output,
            "exit_code": 0,
            "duration": total_duration,
            "error": "",
        }
        state.context["previous"] = merged_output
        state.completed.add(node.id)
        self._persist(state.results, "running")

        wave_idx = state.node_wave.get(node.id, 0)
        self._emit(
            NodeDoneEvent(
                node_id=node.id,
                description=node.description,
                result=map_result,
                wave_idx=wave_idx,
            )
        )
        self._emit(
            ProgressEvent(
                message=f"Map '{node.id}' done — {len(items)} items ({total_duration:.1f}s)"
            )
        )
        self._activate_downstream(node.id, wf, state)

    async def _run_map_child(
        self, child_id: str, task: str, context_header: str | None
    ) -> NodeResult:
        """Run a single map child task under the semaphore."""
        async with self._semaphore:
            return await self._exec_node(child_id, task, context_header)

    # ── Downstream activation helper ──────────────────────────

    @staticmethod
    def _activate_downstream(node_id: str, wf: Workflow, state: RunState) -> None:
        """Decrement pending_deps for dependents and enqueue ready ones."""
        for dep_id in wf.dependents.get(node_id, []):
            if dep_id in state.skipped:
                continue
            state.pending_deps[dep_id] = state.pending_deps.get(dep_id, 0) - 1
            if state.is_ready(dep_id) and dep_id not in state.ready_queue:
                state.ready_queue.append(dep_id)

    # ── Node context header ───────────────────────────────────

    @staticmethod
    def _build_node_context_header(node: Node, workflow: Workflow) -> str:
        """Build context header with graph structure info for a node."""
        lines = [f'You are node "{node.id}" in workflow "{workflow.name}".']
        if node.description:
            lines.append(f"Description: {node.description}")

        if node.depends:
            lines.append("Input from:")
            for dep_id in node.depends:
                dep_node = workflow.node_map.get(dep_id)
                desc = dep_node.description if dep_node else ""
                lines.append(f"  - {dep_id}: {desc}" if desc else f"  - {dep_id}")

        dep_ids = workflow.dependents.get(node.id, [])
        if dep_ids:
            lines.append("Output to:")
            for dep_id in dep_ids:
                dep_node = workflow.node_map.get(dep_id)
                desc = dep_node.description if dep_node else ""
                lines.append(f"  - {dep_id}: {desc}" if desc else f"  - {dep_id}")

        return "\n".join(lines)

    # ── Subprocess execution ──────────────────────────────────

    async def _exec_node(
        self,
        node_id: str,
        task: str,
        context_header: str | None = None,
    ) -> NodeResult:
        """Spawn mocode -p with the filled task template."""
        prompt = f"{context_header}\n\n---\nTask: {task}" if context_header else task
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                self.mocode_cmd,
                "-p",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout,
            )
            duration = time.monotonic() - start
            return NodeResult(
                node_id=node_id,
                task=task,
                output=stdout.decode("utf-8", errors="replace").strip(),
                exit_code=proc.returncode or 0,
                duration=duration,
                error=stderr.decode("utf-8", errors="replace").strip() or None,
            )
        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            return NodeResult(
                node_id=node_id,
                task=task,
                output="",
                exit_code=1,
                duration=duration,
                error=f"timed out after {self.timeout}s",
            )
        except Exception as e:
            duration = time.monotonic() - start
            return NodeResult(
                node_id=node_id,
                task=task,
                output="",
                exit_code=1,
                duration=duration,
                error=str(e),
            )

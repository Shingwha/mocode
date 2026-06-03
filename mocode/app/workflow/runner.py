"""DAGRunner — async event-driven execution engine for Workflow DAG.

Each task node spawns a ``mocode -p`` subprocess. Router nodes evaluate
conditions. Back-edges from routers enable loops. The engine auto-schedules
parallel execution based on dependency satisfaction.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from . import Node, NodeResult, Workflow

from . import NodeResult, fill_template


class DAGRunner:
    """Executes a Workflow DAG by spawning mocode -p for each task node."""

    def __init__(
        self,
        workflow: Workflow,
        mocode_cmd: str = "mocode",
        timeout: int = 300,
        on_wave_start: Callable[[int, int, list[str]], None] | None = None,
        on_node_start: Callable[[str, str], None] | None = None,
        on_node_done: Callable[[str, NodeResult, int], None] | None = None,
        on_node_skip: Callable[[str, str], None] | None = None,
        on_loop_iter: Callable[[str, int, int, NodeResult], None] | None = None,
        on_condition: Callable[[str, bool, str], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        self.workflow = workflow
        self.mocode_cmd = mocode_cmd
        self.timeout = timeout
        self._on_wave_start = on_wave_start
        self._on_node_start = on_node_start
        self._on_node_done = on_node_done
        self._on_node_skip = on_node_skip
        self._on_loop_iter = on_loop_iter
        self._on_condition = on_condition
        self._on_progress = on_progress
        self._context: dict = {}

    def _progress(self, msg: str) -> None:
        if self._on_progress:
            self._on_progress(msg)

    async def run(self, args: dict | None = None) -> list[NodeResult]:
        """Execute the full workflow DAG. Returns all NodeResults."""
        from . import compute_waves

        wf = self.workflow
        wf.status = "running"
        wf.results.clear()

        self._context = {
            "args": args or {},
            "env": dict(os.environ),
            "nodes": {},
            "previous": "",
        }

        node_map = wf.node_map

        # Compute waves for display
        waves = compute_waves(wf)
        total_waves = len(waves)
        node_wave: dict[str, int] = {}
        for idx, wave in enumerate(waves):
            for n in wave:
                node_wave[n.id] = idx

        # Execution state
        pending_deps: dict[str, int] = {n.id: len(n.depends) for n in wf.nodes}
        activated: set[str] = set()  # nodes activated by some path
        completed: set[str] = set()
        skipped: set[str] = set()
        route_counter: dict[str, int] = {}  # "router_id:route_idx" → count
        iteration: dict[str, int] = {n.id: 0 for n in wf.nodes}
        running: dict[str, asyncio.Task] = {}
        task_to_nid: dict[asyncio.Task, str] = {}  # reverse lookup: task → node_id
        announced_waves: set[int] = set()
        completed_waves: set[int] = set()

        def _try_announce_waves() -> None:
            """Announce waves in order, only when all prior waves announced."""
            for w in range(total_waves):
                if w in announced_waves:
                    continue
                # All prior waves must be announced (header already printed)
                if any(pw not in announced_waves for pw in range(w)):
                    break
                # All nodes in this wave must have all depends satisfied
                all_deps_met = all(
                    all(dep in completed for dep in n.depends)
                    for n in waves[w]
                )
                if not all_deps_met:
                    break
                # Announce this wave (flushes buffer before header)
                announced_waves.add(w)
                if self._on_wave_start:
                    wave_nids = [n.id for n in waves[w]]
                    self._on_wave_start(w, total_waves, wave_nids)

        # Activate root nodes
        ready_queue: list[str] = []
        for n in wf.root_nodes:
            activated.add(n.id)
            ready_queue.append(n.id)

        # Circuit breaker: total executions across all nodes
        total_executions = 0
        max_total = wf.max_iterations * len(wf.nodes)

        def _set_previous(node_id: str, output: str) -> None:
            self._context["previous"] = output

        def _store_node_result(node_id: str, nr: NodeResult) -> None:
            self._context["nodes"][node_id] = {
                "output": nr.output,
                "exit_code": nr.exit_code,
                "duration": nr.duration,
                "error": nr.error or "",
            }

        try:
            while ready_queue or running:
                _try_announce_waves()

                # Launch all ready nodes
                for nid in list(ready_queue):
                    if nid in running:
                        continue
                    node = node_map[nid]
                    iteration[nid] += 1

                    if node.type == "router":
                        # Router: evaluate synchronously, don't spawn subprocess
                        self._evaluate_router(
                            node, node_map, completed, activated,
                            pending_deps, ready_queue, iteration,
                            route_counter, skipped, completed_waves,
                            node_wave,
                            announce_wave=_try_announce_waves,
                        )
                    else:
                        # Task: spawn subprocess
                        task_text = fill_template(node.task, self._context)
                        if self._on_node_start:
                            self._on_node_start(nid, node.description)
                        task = asyncio.create_task(self._exec_node(nid, task_text))
                        running[nid] = task
                        task_to_nid[task] = nid
                    ready_queue.remove(nid)

                if not running:
                    if not ready_queue:
                        break
                    continue  # loop back to launch ready_queue items

                # Wait for any task to complete
                done_tasks, _ = await asyncio.wait(
                    running.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Process completed tasks
                for task in done_tasks:
                    # Find node_id via reverse lookup
                    nid = task_to_nid.pop(task, None)
                    if nid is None:
                        continue

                    del running[nid]
                    nr = task.result()
                    total_executions += 1

                    # Store result
                    wf.results.append(nr)
                    _store_node_result(nid, nr)
                    _set_previous(nid, nr.output)
                    completed.add(nid)

                    # Callback
                    wave_idx = node_wave.get(nid, 0)
                    if self._on_node_done:
                        self._on_node_done(nid, nr, wave_idx)

                    # Track wave completion
                    wave_node_ids = [n.id for n in waves[wave_idx]]
                    if all(nid_c in completed for nid_c in wave_node_ids):
                        completed_waves.add(wave_idx)

                    self._progress(f"Node '{nid}' done ({nr.duration:.1f}s)")

                    # Activate downstream dependents
                    for dep_id in wf.dependents.get(nid, []):
                        if dep_id in skipped:
                            continue
                        pending_deps[dep_id] -= 1
                        if pending_deps[dep_id] <= 0:
                            if dep_id not in running and dep_id not in ready_queue:
                                ready_queue.append(dep_id)

                    _try_announce_waves()

                # Circuit breaker
                if total_executions >= max_total:
                    wf.status = "loop_limit"
                    self._progress(
                        f"Workflow execution limit reached ({max_total} total executions)"
                    )
                    # Cancel remaining tasks
                    for t in running.values():
                        t.cancel()
                    break

            # Propagate skips for unactivated nodes
            for n in wf.nodes:
                if n.id not in completed and n.id not in skipped:
                    skipped.add(n.id)
                    if self._on_node_skip:
                        self._on_node_skip(n.id, "not activated")

            if wf.status not in ("error", "loop_limit"):
                wf.status = "done"
            return wf.results

        except Exception:
            wf.status = "error"
            raise

    def _evaluate_router(
        self,
        node: Node,
        node_map: dict,
        completed: set,
        activated: set,
        pending_deps: dict,
        ready_queue: list,
        iteration: dict,
        route_counter: dict,
        skipped: set,
        completed_waves: set | None = None,
        node_wave: dict | None = None,
        announce_wave=None,
    ) -> None:
        """Evaluate a router node's routes and activate targets."""
        # Concatenate outputs of all dependency nodes
        dep_outputs = []
        for dep in node.depends:
            node_data = self._context.get("nodes", {}).get(dep, {})
            dep_outputs.append(node_data.get("output", ""))
        combined = "\n".join(dep_outputs)

        # Evaluate routes (first-match-wins)
        matched = False
        all_targets: set[str] = set()
        for ri, route in enumerate(node.routes):
            route_key = f"{node.id}:{ri}"
            current_count = route_counter.get(route_key, 0)

            # Check max limit
            if route.max > 0 and current_count >= route.max:
                continue

            # Check match
            if route.match is not None:
                if not re.search(route.match, combined):
                    continue

            # Matched!
            route_counter[route_key] = current_count + 1
            matched = True

            if self._on_condition:
                branch_name = f"route {ri}"
                self._on_condition(node.id, True, branch_name)

            # Activate targets
            for target_id in route.to:
                all_targets.add(target_id)
                target_node = node_map.get(target_id)

                if target_id in completed and target_node:
                    # Back-edge — loop back
                    iteration[target_id] += 1
                    completed.discard(target_id)
                    # Allow the target's wave to be re-announced
                    if completed_waves is not None and node_wave is not None:
                        target_wave_idx = node_wave.get(target_id)
                        if target_wave_idx is not None:
                            completed_waves.discard(target_wave_idx)
                    # Reset downstream chain (includes router if in completed)
                    self._reset_downstream(target_id, node_map, completed, pending_deps, activated, skipped)
                    # Router must re-evaluate after back-edge target re-runs
                    completed.discard(node.id)
                    pending_deps[node.id] = len(node.depends) - 1  # -1: target re-satisfies
                    # Reset the target's own deps, then activate normally
                    pending_deps[target_id] = len(target_node.depends)
                    self._activate_target(target_id, ready_queue, activated, skipped, pending_deps, announce_wave)

                    # Loop iter callback
                    max_iter = route.max if route.max > 0 else 0
                    iter_result = NodeResult(
                        node_id=target_id,
                        task="",
                        output="",
                        exit_code=0,
                        duration=0,
                        iteration=iteration[target_id],
                    )
                    if self._on_loop_iter:
                        self._on_loop_iter(target_id, iteration[target_id], max_iter, iter_result)
                else:
                    # Forward edge
                    self._activate_target(target_id, ready_queue, activated, skipped, pending_deps, announce_wave)
            break  # first-match-wins

        if not matched:
            if self._on_condition:
                self._on_condition(node.id, False, "no match")

        # Mark router as completed
        completed.add(node.id)

        # Mark all downstream nodes not activated by any route as skipped
        for dep_id in self.workflow.dependents.get(node.id, []):
            if dep_id not in all_targets and dep_id not in activated:
                skipped.add(dep_id)
                if self._on_node_skip:
                    self._on_node_skip(dep_id, "not activated by router")
                self._propagate_skip(dep_id, node_map, activated, skipped)

    def _activate_target(
        self,
        target_id: str,
        ready_queue: list[str],
        activated: set[str],
        skipped: set[str],
        pending_deps: dict[str, int],
        announce_wave: Callable | None = None,
    ) -> None:
        """Common activation: mark activated, decrement deps, enqueue if ready."""
        activated.add(target_id)
        skipped.discard(target_id)
        pending_deps[target_id] -= 1
        if pending_deps[target_id] <= 0 and target_id not in ready_queue:
            ready_queue.append(target_id)
            if announce_wave:
                announce_wave()

    def _reset_downstream(
        self,
        node_id: str,
        node_map: dict,
        completed: set,
        pending_deps: dict,
        activated: set,
        skipped: set,
    ) -> None:
        """Reset all downstream nodes of a back-edge target."""
        for child_id in self.workflow.dependents.get(node_id, []):
            if child_id in completed:
                completed.discard(child_id)
                child_node = node_map.get(child_id)
                if child_node:
                    pending_deps[child_id] = len(child_node.depends)
                activated.discard(child_id)
                skipped.discard(child_id)
                self._reset_downstream(child_id, node_map, completed, pending_deps, activated, skipped)

    def _propagate_skip(
        self,
        node_id: str,
        node_map: dict,
        activated: set,
        skipped: set,
    ) -> None:
        """Mark a node and all its downstream as skipped."""
        for child_id in self.workflow.dependents.get(node_id, []):
            if child_id not in activated and child_id not in skipped:
                skipped.add(child_id)
                if self._on_node_skip:
                    self._on_node_skip(child_id, "dependency skipped")
                self._propagate_skip(child_id, node_map, activated, skipped)

    async def _exec_node(self, node_id: str, task: str) -> NodeResult:
        """Spawn mocode -p with the filled task template."""
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                self.mocode_cmd,
                "-p",
                task,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            duration = time.monotonic() - start
            output = stdout.decode("utf-8", errors="replace").strip()
            error_msg = stderr.decode("utf-8", errors="replace").strip() or None
            return NodeResult(
                node_id=node_id,
                task=task,
                output=output,
                exit_code=proc.returncode or 0,
                duration=duration,
                error=error_msg,
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

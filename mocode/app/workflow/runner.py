"""DAGRunner — async event-driven execution engine for Workflow DAG.

Each task node spawns a ``mocode -p`` subprocess. Router nodes evaluate
conditions. Back-edges from routers enable loops. Nodes execute serially
(max_concurrency=1) to avoid overloading LLM APIs.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .models import Node, NodeResult, Workflow

from .models import NodeResult, fill_template


class DAGRunner:
    """Executes a Workflow DAG by spawning mocode -p for each task node."""

    def __init__(
        self,
        workflow: Workflow,
        mocode_cmd: str = "mocode",
        timeout: int = 300,
        node_context: bool = True,
        on_wave_start: Callable[[int, int, list[str]], None] | None = None,
        on_node_start: Callable[[str, str], None] | None = None,
        on_node_done: Callable[[str, NodeResult, int], None] | None = None,
        on_node_skip: Callable[[str, str], None] | None = None,
        on_loop_iter: Callable[[str, int, int, NodeResult], None] | None = None,
        on_condition: Callable[[str, bool, str, list[str]], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        self.workflow = workflow
        self.mocode_cmd = mocode_cmd
        self.timeout = timeout
        self.node_context = node_context
        self._on_wave_start = on_wave_start
        self._on_node_start = on_node_start
        self._on_node_done = on_node_done
        self._on_node_skip = on_node_skip
        self._on_loop_iter = on_loop_iter
        self._on_condition = on_condition
        self._on_progress = on_progress

    def _progress(self, msg: str) -> None:
        if self._on_progress:
            self._on_progress(msg)

    def _build_node_context_header(self, node: Node) -> str:
        """Build a context header with graph structure info for a node."""
        wf = self.workflow

        lines = [
            f'You are node "{node.id}" in workflow "{wf.name}".',
        ]

        if node.description:
            lines.append(f"Description: {node.description}")

        if node.depends:
            lines.append("Input from:")
            for dep_id in node.depends:
                dep_node = wf.node_map.get(dep_id)
                dep_desc = dep_node.description if dep_node else ""
                if dep_desc:
                    lines.append(f"  - {dep_id}: {dep_desc}")
                else:
                    lines.append(f"  - {dep_id}")

        dependent_ids = wf.dependents.get(node.id, [])
        if dependent_ids:
            lines.append("Output to:")
            for dep_id in dependent_ids:
                dep_node = wf.node_map.get(dep_id)
                dep_desc = dep_node.description if dep_node else ""
                if dep_desc:
                    lines.append(f"  - {dep_id}: {dep_desc}")
                else:
                    lines.append(f"  - {dep_id}")

        return "\n".join(lines)

    # ── Per-run state initialization ──────────────────────────

    def _init_run(self, args: dict | None = None) -> None:
        """Initialize per-run state as instance variables."""
        from .graph import compute_waves

        wf = self.workflow

        self._context: dict = {
            "args": args or {},
            "env": dict(os.environ),
            "nodes": {},
            "previous": "",
        }

        waves = compute_waves(wf)
        self._waves = waves
        self._total_waves = len(waves)
        self._node_wave: dict[str, int] = {}
        for idx, wave in enumerate(waves):
            for n in wave:
                self._node_wave[n.id] = idx

        self._pending_deps: dict[str, int] = {n.id: len(n.depends) for n in wf.nodes}
        self._activated: set[str] = set()
        self._completed: set[str] = set()
        self._skipped: set[str] = set()
        self._route_counter: dict[str, int] = {}
        self._iteration: dict[str, int] = {n.id: 0 for n in wf.nodes}
        self._announced_waves: set[int] = set()
        self._completed_waves: set[int] = set()
        self._ready_queue: list[str] = []
        self._total_executions = 0
        self._max_total = wf.max_iterations * len(wf.nodes)
        self._in_loop: bool = False

    # ── Main execution loop ───────────────────────────────────

    async def run(self, args: dict | None = None) -> list[NodeResult]:
        """Execute the full workflow DAG. Returns all NodeResults."""
        wf = self.workflow
        wf.status = "running"
        wf.results.clear()

        self._init_run(args)

        # Activate root nodes
        for n in wf.root_nodes:
            self._activated.add(n.id)
            self._ready_queue.append(n.id)

        try:
            while self._ready_queue:
                if not self._in_loop:
                    self._try_announce_waves()

                # Execute one node at a time (serial)
                nid = self._ready_queue.pop(0)
                node = wf.node_map[nid]
                self._iteration[nid] += 1

                if node.type == "router":
                    self._evaluate_router(node)
                    # Circuit breaker check after router (loops increment _total_executions)
                    if self._total_executions >= self._max_total:
                        wf.status = "loop_limit"
                        self._progress(
                            f"Workflow execution limit reached ({self._max_total} total executions)"
                        )
                        break
                    continue

                task_text = fill_template(node.task, self._context)
                if self._on_node_start:
                    self._on_node_start(nid, node.description)
                context_header = (
                    self._build_node_context_header(node)
                    if self.node_context else None
                )
                nr = await self._exec_node(nid, task_text, context_header)
                self._total_executions += 1

                # Store result
                wf.results.append(nr)
                self._context["nodes"][nid] = {
                    "output": nr.output,
                    "exit_code": nr.exit_code,
                    "duration": nr.duration,
                    "error": nr.error or "",
                }
                self._context["previous"] = nr.output
                self._completed.add(nid)

                # Callback
                wave_idx = self._node_wave.get(nid, 0)
                if self._on_node_done:
                    self._on_node_done(nid, nr, wave_idx)

                # Track wave completion
                wave_node_ids = [n.id for n in self._waves[wave_idx]]
                if all(nid_c in self._completed for nid_c in wave_node_ids):
                    self._completed_waves.add(wave_idx)

                self._progress(f"Node '{nid}' done ({nr.duration:.1f}s)")

                # Activate downstream dependents
                for dep_id in wf.dependents.get(nid, []):
                    if dep_id in self._skipped:
                        continue
                    self._pending_deps[dep_id] -= 1
                    if self._pending_deps[dep_id] <= 0 and dep_id not in self._ready_queue:
                        self._ready_queue.append(dep_id)

                if not self._in_loop:
                    self._try_announce_waves()

                # Circuit breaker
                if self._total_executions >= self._max_total:
                    wf.status = "loop_limit"
                    self._progress(
                        f"Workflow execution limit reached ({self._max_total} total executions)"
                    )
                    break

            # Propagate skips for unactivated nodes
            for n in wf.nodes:
                if n.id not in self._completed and n.id not in self._skipped:
                    self._skipped.add(n.id)
                    if self._on_node_skip:
                        self._on_node_skip(n.id, "not activated")

            if wf.status not in ("error", "loop_limit"):
                wf.status = "done"
            return wf.results

        except Exception:
            wf.status = "error"
            raise

    # ── Wave announcement ─────────────────────────────────────

    def _try_announce_waves(self) -> None:
        """Announce waves in order, only when all prior waves announced."""
        for w in range(self._total_waves):
            if w in self._announced_waves:
                continue
            if any(pw not in self._announced_waves for pw in range(w)):
                break
            all_deps_met = all(
                all(dep in self._completed for dep in n.depends)
                for n in self._waves[w]
            )
            if not all_deps_met:
                break
            self._announced_waves.add(w)
            if self._on_wave_start:
                wave_nids = [n.id for n in self._waves[w]]
                self._on_wave_start(w, self._total_waves, wave_nids)

    # ── Router evaluation ─────────────────────────────────────

    def _evaluate_router(self, node: Node) -> None:
        """Evaluate a router node's routes and activate targets."""
        wf = self.workflow

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
            current_count = self._route_counter.get(route_key, 0)

            if route.max > 0 and current_count >= route.max:
                continue

            if route.match is not None:
                if not re.search(route.match, combined):
                    continue

            # Matched!
            self._route_counter[route_key] = current_count + 1
            matched = True

            if self._on_condition:
                self._on_condition(node.id, True, f"route {ri}", route.to)

            for target_id in route.to:
                all_targets.add(target_id)
                target_node = wf.node_map.get(target_id)

                if target_id in self._completed and target_node:
                    # Back-edge — loop back
                    self._in_loop = True
                    self._completed.discard(target_id)
                    # Allow the target's wave to be re-announced
                    target_wave_idx = self._node_wave.get(target_id)
                    if target_wave_idx is not None:
                        self._completed_waves.discard(target_wave_idx)
                        for later_w in range(target_wave_idx, self._total_waves):
                            self._announced_waves.discard(later_w)
                    self._reset_downstream(target_id)
                    # Router must re-evaluate after back-edge target re-runs
                    self._completed.discard(node.id)
                    self._pending_deps[node.id] = len(node.depends) - 1
                    self._pending_deps[target_id] = len(target_node.depends)
                    self._activate_target(target_id)

                    retry_count = self._route_counter[route_key]
                    max_iter = route.max if route.max > 0 else 0
                    iter_result = NodeResult(
                        node_id=target_id,
                        task="",
                        output="",
                        exit_code=0,
                        duration=0,
                        iteration=retry_count,
                    )
                    if self._on_loop_iter:
                        self._on_loop_iter(target_id, retry_count, max_iter, iter_result)
                else:
                    if self._in_loop:
                        self._in_loop = False
                    self._activate_target(target_id)
            break  # first-match-wins

        if not matched:
            if self._on_condition:
                self._on_condition(node.id, False, "no match", [])

        # Mark router as completed
        self._completed.add(node.id)

        # Mark all downstream nodes not activated by any route as skipped
        for dep_id in wf.dependents.get(node.id, []):
            if dep_id not in all_targets and dep_id not in self._activated:
                self._skipped.add(dep_id)
                self._pending_deps[dep_id] -= 1
                if self._on_node_skip:
                    self._on_node_skip(dep_id, "not activated by router")
                self._propagate_skip(dep_id)

    # ── Node state helpers ────────────────────────────────────

    def _activate_target(self, target_id: str) -> None:
        """Mark activated, decrement deps, enqueue if ready."""
        self._activated.add(target_id)
        self._skipped.discard(target_id)
        self._pending_deps[target_id] -= 1
        if self._pending_deps[target_id] <= 0 and target_id not in self._ready_queue:
            self._ready_queue.append(target_id)
            self._try_announce_waves()

    def _reset_downstream(self, node_id: str) -> None:
        """Reset all downstream nodes of a back-edge target."""
        wf = self.workflow
        for child_id in wf.dependents.get(node_id, []):
            if child_id in self._completed or child_id in self._skipped:
                self._completed.discard(child_id)
                self._skipped.discard(child_id)
                child_node = wf.node_map.get(child_id)
                if child_node:
                    self._pending_deps[child_id] = len(child_node.depends)
                    completed_deps = sum(1 for d in child_node.depends if d in self._completed)
                    self._pending_deps[child_id] -= completed_deps
                self._activated.discard(child_id)
                self._reset_downstream(child_id)

    def _propagate_skip(self, node_id: str) -> None:
        """Mark a node and all its downstream as skipped."""
        for child_id in self.workflow.dependents.get(node_id, []):
            if child_id not in self._activated and child_id not in self._skipped:
                self._skipped.add(child_id)
                if self._on_node_skip:
                    self._on_node_skip(child_id, "dependency skipped")
                self._propagate_skip(child_id)

    # ── Subprocess execution ──────────────────────────────────

    async def _exec_node(
        self, node_id: str, task: str, context_header: str | None = None,
    ) -> NodeResult:
        """Spawn mocode -p with the filled task template."""
        if context_header:
            prompt = f"{context_header}\n\n---\nTask: {task}"
        else:
            prompt = task

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

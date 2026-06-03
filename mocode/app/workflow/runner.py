"""WorkflowRunner — async execution engine for Workflow with goto-based control flow.

Each step spawns a ``mocode -p`` subprocess. Supports sequential (steps),
parallel lanes, and goto-based routing at both step and phase level.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from . import Lane, Phase, Step, Workflow

from . import GotoRule, StepResult, fill_template


class WorkflowRunner:
    """Executes a Workflow by spawning mocode -p for each step."""

    def __init__(
        self,
        workflow: Workflow,
        mocode_cmd: str = "mocode",
        timeout: int = 300,
        on_progress: Callable[[str], None] | None = None,
        on_step_done: Callable[..., None] | None = None,
    ):
        self.workflow = workflow
        self.mocode_cmd = mocode_cmd
        self.timeout = timeout
        self._on_progress = on_progress
        self._on_step_done = on_step_done
        self._context: dict = {}

        # Runtime counters
        self._phase_counter: dict[str, int] = {}
        self._wf_phase_entry_count: int = 0
        self._goto_counter: dict[str, int] = {}

    def _progress(self, msg: str) -> None:
        if self._on_progress:
            self._on_progress(msg)

    def _step_done(self, sr: StepResult, phase_name: str, lane_name: str | None = None) -> None:
        if self._on_step_done:
            self._on_step_done(sr, phase_name, lane_name)

    # ═══════════════════════════════════════════════════════════
    # 3.1 Top-level control flow
    # ═══════════════════════════════════════════════════════════

    async def run(self, args: dict | None = None) -> list[StepResult]:
        """Execute the full workflow. Returns all StepResults."""
        wf = self.workflow
        wf.status = "running"
        wf.results.clear()
        wf.phase_index = 0
        wf.step_index = 0

        self._context = {
            "args": args or {},
            "env": dict(os.environ),
            "steps": {},
            "lanes": {},
            "phases": {},
        }

        self._phase_counter.clear()
        self._wf_phase_entry_count = 0
        self._goto_counter.clear()

        current_phase_id = wf.phases[0].id if wf.phases else None

        try:
            while current_phase_id is not None:
                phase = self._find_phase(current_phase_id)
                if phase is None:
                    raise ValueError(f"Phase '{current_phase_id}' not found")

                wf.phase_index = wf.phases.index(phase)

                # ── Circuit breaker ──
                self._wf_phase_entry_count += 1
                if self._wf_phase_entry_count > wf.max_iterations:
                    wf.status = "loop_limit"
                    self._progress(
                        f"Workflow loop limit reached after {wf.max_iterations} phase executions"
                    )
                    break

                pid = phase.id or f"__phase_{wf.phase_index}"
                self._phase_counter[pid] = self._phase_counter.get(pid, 0) + 1
                phase_limit = phase.max_iterations or wf.max_iterations
                if self._phase_counter[pid] > phase_limit:
                    wf.status = "loop_limit"
                    self._progress(
                        f"Phase '{phase.name}' loop limit reached after {phase_limit} entries"
                    )
                    break

                # ── Execute Phase ──
                if phase.lanes:
                    next_target = await self._run_lanes(phase)
                else:
                    next_target = await self._run_steps(phase)

                # ── Determine next phase ──
                if next_target == "__end__":
                    break
                elif next_target and next_target.startswith("phase."):
                    current_phase_id = next_target.split(".", 1)[1]
                    continue
                elif next_target == "next":
                    idx = wf.phases.index(phase)
                    current_phase_id = (
                        wf.phases[idx + 1].id if idx + 1 < len(wf.phases) else None
                    )
                    continue
                elif next_target == "end":
                    break
                else:
                    current_phase_id = None

            if wf.status not in ("error", "loop_limit"):
                wf.status = "done"
            return wf.results

        except Exception:
            wf.status = "error"
            raise

    # ═══════════════════════════════════════════════════════════
    # 3.2 Sequential mode — _run_steps
    # ═══════════════════════════════════════════════════════════

    async def _run_steps(self, phase: Phase) -> str | None:
        """Run phase.steps sequentially. Returns goto target or None."""
        si = 0
        while si < len(phase.steps):
            step = phase.steps[si]
            wf = self.workflow
            wf.step_index = si
            self._progress(f"Step {si + 1}/{len(phase.steps)}: {step.task[:40]}")

            sr = await self._exec_step(step, wf.phases.index(phase), si)
            wf.results.append(sr)
            self._store_step(step, sr, lane_id=None)
            self._context["previous"] = sr.output

            self._step_done(sr, phase.name)

            # Parse goto
            target = self._resolve_goto(
                step.goto, sr.output, f"step_{phase.id}_{step.id}"
            )
            if target == "next":
                si += 1
            elif target == "end":
                break
            elif target == "__end__":
                self._progress("→ goto: __end__")
                return "__end__"
            elif target.startswith("phase."):
                self._progress(f"→ goto: {target}")
                self._store_phase_output(phase)
                return target
            else:
                # step_id: jump within same phase
                self._progress(f"→ goto: {target}")
                idx = self._find_step_index(phase.steps, target)
                if idx is not None:
                    si = idx
                else:
                    self._progress(f"Warning: step '{target}' not found, continuing")
                    si += 1

        # All steps done → check Phase.goto
        self._store_phase_output(phase)
        return self._resolve_goto(phase.goto, "", f"phase_{phase.id}")

    # ═══════════════════════════════════════════════════════════
    # 3.3 Parallel lanes mode — _run_lanes
    # ═══════════════════════════════════════════════════════════

    async def _run_lanes(self, phase: Phase) -> str | None:
        """Run all lanes concurrently. Returns goto target or None."""
        self._progress(f"Running {len(phase.lanes)} lanes in parallel")

        async def _run_single_lane(lane: Lane, lane_idx: int) -> str | None:
            """Execute one lane. Returns a target if cross-phase jump needed."""
            lane_output: str | None = None
            si = 0
            while si < len(lane.steps):
                step = lane.steps[si]
                sr = await self._exec_step(step, wf.phases.index(phase), si)
                sr.lane = lane.id
                wf.results.append(sr)
                self._store_step(step, sr, lane_id=lane.id)
                lane_output = sr.output

                self._step_done(sr, phase.name, lane.name)

                target = self._resolve_goto(
                    step.goto, sr.output,
                    f"step_{phase.id}_{lane.id}_{step.id}",
                )
                if target == "next":
                    si += 1
                elif target == "end":
                    break
                elif target == "__end__":
                    self._progress(f"→ goto: __end__ (lane '{lane.name}')")
                    return "__end__"
                elif target.startswith("phase."):
                    self._progress(f"→ goto: {target} (lane '{lane.name}')")
                    return target  # cross-phase jump
                else:
                    # step_id jump within same lane
                    self._progress(f"→ goto: {target} (lane '{lane.name}')")
                    idx = self._find_step_index(lane.steps, target)
                    si = idx if idx is not None else si + 1

            # Store lane output
            if lane.id:
                self._context.setdefault("lanes", {})[lane.id] = {
                    "output": lane_output or "",
                }
            return None  # lane completed normally

        wf = self.workflow
        tasks = [
            asyncio.create_task(_run_single_lane(lane, i))
            for i, lane in enumerate(phase.lanes)
        ]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        # Check if any lane triggered a cross-phase jump
        cross_phase_target = None
        for target in raw:
            if isinstance(target, BaseException):
                if not isinstance(target, asyncio.CancelledError):
                    self._progress(f"Lane error: {target}")
                continue
            if target and (target.startswith("phase.") or target == "__end__"):
                cross_phase_target = target
                # Cancel remaining running lanes
                for t in tasks:
                    if not t.done():
                        t.cancel()
                self._progress("→ lane cancelled (other lane triggered cross-phase jump)")
                break

        if cross_phase_target:
            self._store_phase_output(phase)
            return cross_phase_target

        # All lanes done → check Phase.goto
        self._store_phase_output(phase)
        return self._resolve_goto(phase.goto, "", f"phase_{phase.id}")

    # ═══════════════════════════════════════════════════════════
    # 3.4 Goto resolution engine
    # ═══════════════════════════════════════════════════════════

    def _resolve_goto(self, rules: list[GotoRule], output: str, key_prefix: str) -> str:
        """Return the target of the first matching rule, or 'next'."""
        for rule in rules:
            rule_key = f"{key_prefix}_to_{rule.to}"
            current_count = self._goto_counter.get(rule_key, 0)

            if rule.max > 0 and current_count >= rule.max:
                continue  # hit limit, skip this rule

            if rule.match is None:
                self._goto_counter[rule_key] = current_count + 1
                return rule.to

            if re.search(rule.match, output):
                self._goto_counter[rule_key] = current_count + 1
                return rule.to

        return "next"

    # ═══════════════════════════════════════════════════════════
    # 3.5 Helper methods
    # ═══════════════════════════════════════════════════════════

    def _find_phase(self, phase_id: str) -> Phase | None:
        for p in self.workflow.phases:
            if p.id == phase_id:
                return p
        return None

    def _find_step_index(self, steps: list[Step], step_id: str) -> int | None:
        for i, s in enumerate(steps):
            if s.id == step_id:
                return i
        return None

    def _store_step(self, step: Step, sr: StepResult, lane_id: str | None) -> None:
        if not step.id:
            return
        entry = {
            "output": sr.output,
            "exit_code": sr.exit_code,
            "duration": sr.duration,
            "error": sr.error or "",
        }
        if lane_id:
            self._context.setdefault("steps_by_lane", {}).setdefault(lane_id, {})[
                step.id
            ] = entry
        else:
            self._context.setdefault("steps", {})[step.id] = entry

    def _store_phase_output(self, phase: Phase) -> None:
        if not phase.id:
            return
        if phase.lanes:
            outputs = []
            for lane in phase.lanes:
                if lane.id and lane.id in self._context.get("lanes", {}):
                    outputs.append(self._context["lanes"][lane.id]["output"])
            combined = "\n".join(outputs)
        else:
            phase_results = [
                r
                for r in self.workflow.results
                if r.phase_index == self.workflow.phases.index(phase)
            ]
            combined = phase_results[-1].output if phase_results else ""
        self._context.setdefault("phases", {})[phase.id] = {"output": combined}

    # ═══════════════════════════════════════════════════════════
    # Single step execution
    # ═══════════════════════════════════════════════════════════

    async def _exec_step(self, step: Step, pi: int, si: int) -> StepResult:
        """Spawn mocode -p with the filled task template."""
        task = fill_template(step.task, self._context)
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
            return StepResult(
                phase_index=pi,
                step_index=si,
                task=task,
                output=output,
                exit_code=proc.returncode or 0,
                duration=duration,
                error=error_msg,
            )
        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            return StepResult(
                phase_index=pi,
                step_index=si,
                task=task,
                output="",
                exit_code=1,
                duration=duration,
                error=f"timed out after {self.timeout}s",
            )
        except Exception as e:
            duration = time.monotonic() - start
            return StepResult(
                phase_index=pi,
                step_index=si,
                task=task,
                output="",
                exit_code=1,
                duration=duration,
                error=str(e),
            )

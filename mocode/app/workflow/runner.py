"""WorkflowRunner — async execution engine for Workflow.

Each step spawns a ``mocode -p`` subprocess. Supports serial, parallel,
and loop (retry + halt_if) modes per phase.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from . import Phase, Step, Workflow

from . import StepResult, fill_template


class WorkflowRunner:
    """Executes a Workflow by spawning mocode -p for each step."""

    def __init__(
        self,
        workflow: Workflow,
        mocode_cmd: str = "mocode",
        timeout: int = 300,
        on_progress: Callable[[str], None] | None = None,
        on_step_done: Callable[[str, int, float], None] | None = None,
    ):
        self.workflow = workflow
        self.mocode_cmd = mocode_cmd
        self.timeout = timeout
        self._on_progress = on_progress
        self._on_step_done = on_step_done
        self._context: dict = {}

    def _progress(self, msg: str) -> None:
        if self._on_progress:
            self._on_progress(msg)

    def _step_done(self, phase_name: str, exit_code: int, duration: float) -> None:
        if self._on_step_done:
            self._on_step_done(phase_name, exit_code, duration)

    async def run(self, args: dict | None = None) -> list[StepResult]:
        """Execute the full workflow. Returns all StepResults."""
        wf = self.workflow
        wf.status = "running"
        wf.results.clear()
        wf.phase_index = 0

        self._context = {
            "args": args or {},
            "env": dict(os.environ),
            "steps": {},
            "phases": {},
        }

        try:
            for pi, phase in enumerate(wf.phases):
                wf.phase_index = pi
                wf.step_index = 0
                self._progress(phase.name)

                if phase.parallel:
                    results = await self._run_parallel(phase, pi)
                elif phase.max_attempts > 1:
                    results = await self._run_loop(phase, pi)
                else:
                    results = await self._run_serial(phase, pi)

                # Store phase output
                if phase.id:
                    combined = "\n".join(r.output for r in results if r.output)
                    self._context.setdefault("phases", {})[phase.id] = {
                        "output": combined,
                    }

                # Stop if any step failed
                if any(r.exit_code != 0 for r in results):
                    wf.status = "error"
                    return wf.results

            wf.status = "done"
            return wf.results

        except Exception:
            wf.status = "error"
            raise

    # ── Serial ────────────────────────────────────────────────

    async def _run_serial(self, phase: Phase, pi: int) -> list[StepResult]:
        results = []
        total = len(phase.steps)
        for si, step in enumerate(phase.steps):
            self.workflow.step_index = si
            self._progress(f"Phase {pi + 1} · Step {si + 1}/{total}: {phase.name}")
            sr = await self._exec_step(step, pi, si)
            self.workflow.results.append(sr)
            results.append(sr)
            self._store_step(step, sr)
            self._context["previous"] = sr.output
            self._progress(f"Phase {pi + 1} · Step {si + 1}/{total} ✓ ({sr.duration:.1f}s)")
            self._step_done(phase.name, sr.exit_code, sr.duration)
            if sr.exit_code != 0:
                break
        return results

    # ── Parallel ──────────────────────────────────────────────

    async def _run_parallel(self, phase: Phase, pi: int) -> list[StepResult]:
        self._progress(f"Phase {pi + 1} · Running {len(phase.steps)} steps in parallel")
        tasks = [self._exec_step(step, pi, si) for si, step in enumerate(phase.steps)]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for i, r in enumerate(raw):
            step = phase.steps[i]
            if isinstance(r, BaseException):
                sr = StepResult(
                    phase_index=pi,
                    step_index=i,
                    task=step.task,
                    output="",
                    exit_code=1,
                    duration=0.0,
                    error=str(r),
                )
            else:
                sr = r
            self.workflow.results.append(sr)
            results.append(sr)
            self._store_step(step, sr)
            self._step_done(phase.name, sr.exit_code, sr.duration)
            self._context["previous"] = results[-1].output
        ok = sum(1 for r in results if r.exit_code == 0)
        self._progress(f"Phase {pi + 1} · Parallel done: {ok}/{len(results)} ok")
        return results

    # ── Loop (retry + halt_if) ────────────────────────────────

    async def _run_loop(self, phase: Phase, pi: int) -> list[StepResult]:
        results = []
        total = len(phase.steps)
        for attempt in range(phase.max_attempts):
            self._progress(f"Phase {pi + 1} · Attempt {attempt + 1}/{phase.max_attempts}")
            attempt_results = []
            for si, step in enumerate(phase.steps):
                self.workflow.step_index = si
                self._progress(f"Phase {pi + 1} · Step {si + 1}/{total}: {phase.name}")
                sr = await self._exec_step(step, pi, si)
                self.workflow.results.append(sr)
                attempt_results.append(sr)
                self._store_step(step, sr)
                self._context["previous"] = sr.output
                self._progress(f"Phase {pi + 1} · Step {si + 1}/{total} ✓ ({sr.duration:.1f}s)")
                self._step_done(phase.name, sr.exit_code, sr.duration)

            results = attempt_results

            # Check halt_if against last step output
            if phase.halt_if and attempt_results:
                last_output = attempt_results[-1].output
                if re.search(phase.halt_if, last_output):
                    break

            # All succeeded — no need to retry
            if all(r.exit_code == 0 for r in attempt_results):
                break

        return results

    # ── Single step execution ─────────────────────────────────

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

    # ── Context helpers ───────────────────────────────────────

    def _store_step(self, step: Step, sr: StepResult) -> None:
        if step.id:
            self._context.setdefault("steps", {})[step.id] = {
                "output": sr.output,
                "exit_code": sr.exit_code,
                "duration": sr.duration,
                "error": sr.error or "",
            }

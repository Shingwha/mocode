"""Workflow engine — YAML-driven multi-step task execution.

Usage:
    from mocode.workflow import Workflow, WorkflowRegistry

    wf = Workflow.from_yaml(Path("my-workflow.yaml"))
    registry = WorkflowRegistry([Path.home() / ".mocode" / "workflows"])
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ── Data models ──────────────────────────────────────────────


@dataclass
class Step:
    id: str = ""
    task: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        return cls(id=data.get("id", ""), task=data.get("task", ""))


@dataclass
class Phase:
    id: str = ""
    name: str = ""
    steps: list[Step] = field(default_factory=list)
    parallel: bool = False
    max_attempts: int = 1
    halt_if: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Phase:
        steps = [Step.from_dict(s) for s in data.get("steps", [])]
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            steps=steps,
            parallel=data.get("parallel", False),
            max_attempts=data.get("max_attempts", 1),
            halt_if=data.get("halt_if"),
        )


@dataclass
class StepResult:
    phase_index: int
    step_index: int
    task: str
    output: str
    exit_code: int
    duration: float
    error: str | None = None


# ── Template filling ─────────────────────────────────────────

_RE_PLACEHOLDER = re.compile(r"\{(\w+(?:\.\w+)*)\}")


def fill_template(template: str, context: dict) -> str:
    """Replace {a.b.c} placeholders by dot-path lookup in context dict."""

    def _replace(m: re.Match) -> str:
        path = m.group(1)
        if path == "previous":
            prev = context.get("previous")
            return str(prev) if prev is not None else m.group(0)
        parts = path.split(".", 1)
        if len(parts) == 2:
            bucket, rest = parts
            obj = context.get(bucket)
            if isinstance(obj, dict):
                return _dot_lookup(obj, rest, m.group(0))
        return m.group(0)

    return _RE_PLACEHOLDER.sub(_replace, template)


def _dot_lookup(obj: dict, path: str, default: str) -> str:
    """Walk 'a.b.c' path into nested dicts. Return default on failure."""
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return default
    return str(obj) if obj is not None else default


# ── Workflow ─────────────────────────────────────────────────


@dataclass
class Workflow:
    name: str
    description: str
    phases: list[Phase]
    path: Path | None = None

    # Runtime state
    status: str = "idle"  # idle | running | done | error
    phase_index: int = 0
    step_index: int = 0
    results: list[StepResult] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> Workflow:
        """Parse a workflow YAML file."""
        import yaml

        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        phases = [Phase.from_dict(p) for p in data.get("phases", [])]
        return cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            phases=phases,
            path=path,
        )

    # ── Query methods ────────────────────────────────────────

    @property
    def current_phase(self) -> Phase | None:
        if 0 <= self.phase_index < len(self.phases):
            return self.phases[self.phase_index]
        return None

    @property
    def current_step(self) -> Step | None:
        phase = self.current_phase
        if phase and 0 <= self.step_index < len(phase.steps):
            return phase.steps[self.step_index]
        return None

    @property
    def total_phases(self) -> int:
        return len(self.phases)

    def total_steps(self) -> int:
        return sum(len(p.steps) for p in self.phases)

    def completed_steps(self) -> int:
        return len(self.results)

    def progress_bar(self) -> str:
        pi = self.phase_index + 1
        tp = self.total_phases
        phase = self.current_phase
        if phase:
            si = self.step_index + 1
            ts = len(phase.steps)
            return f"Phase {pi}/{tp} · Step {si}/{ts}"
        return f"Phase {pi}/{tp}"

    def summary(self) -> str:
        lines = [f"Workflow: {self.name}", f"Status: {self.status}"]
        for r in self.results:
            phase = self.phases[r.phase_index]
            step = phase.steps[r.step_index]
            status = "OK" if r.exit_code == 0 else "FAIL"
            task_preview = step.task[:40] if step.task else "(empty)"
            lines.append(
                f"  [{status}] Phase '{phase.name}' · {task_preview}: {r.duration:.1f}s"
            )
        return "\n".join(lines)

    def detailed_summary(self, max_lines: int = 10) -> str:
        lines = [f"Workflow: {self.name}", f"Status: {self.status}"]
        for r in self.results:
            phase = self.phases[r.phase_index]
            step = phase.steps[r.step_index]
            status = "OK" if r.exit_code == 0 else "FAIL"
            task_preview = step.task[:60] if step.task else "(empty)"
            lines.append(
                f"  [{status}] Phase '{phase.name}' · {task_preview} ({r.duration:.1f}s)"
            )
            if r.output:
                output_lines = r.output.splitlines()
                for ol in output_lines[:max_lines]:
                    lines.append(f"      {ol}")
                if len(output_lines) > max_lines:
                    lines.append(f"      ... ({len(output_lines) - max_lines} more lines)")
            if r.error:
                lines.append(f"      Error: {r.error[:100]}")
        return "\n".join(lines)


# ── Registry ─────────────────────────────────────────────────


class WorkflowRegistry:
    """Discovers YAML workflow files from configured directories."""

    def __init__(self, dirs: list[Path] | None = None):
        self._dirs: list[Path] = list(dirs) if dirs else []
        self._workflows: dict[str, Workflow] = {}
        self.discover()

    def discover(self) -> None:
        """Scan directories for *.yaml / *.yml files."""
        self._workflows.clear()
        for d in self._dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix in (".yaml", ".yml"):
                    wf = self._load(f)
                    if wf:
                        self._workflows[wf.name] = wf

    def _load(self, path: Path) -> Workflow | None:
        try:
            return Workflow.from_yaml(path)
        except Exception:
            return None

    def list(self) -> list[Workflow]:
        return list(self._workflows.values())

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def names(self) -> list[str]:
        return list(self._workflows.keys())

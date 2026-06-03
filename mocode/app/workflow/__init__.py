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
class GotoRule:
    """A single goto rule: if match (regex) hits, jump to target.

    Target address syntax:
        "next"          → next step (step context) / next phase (phase context)
        "end"           → end current lane/phase
        "__end__"       → terminate entire workflow
        "step_id"       → jump to step within same lane
        "phase.xxx"     → jump to phase with id "xxx"
    """

    match: str | None = None  # regex, None = default/fallback
    to: str = "next"  # target address
    max: int = 0  # max hits for this path, 0 = unlimited

    @classmethod
    def from_dict(cls, data: dict) -> GotoRule:
        return cls(
            match=data.get("match"),
            to=data.get("to", "next"),
            max=data.get("max", 0),
        )


@dataclass
class Step:
    id: str = ""
    task: str = ""
    goto: list[GotoRule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        return cls(
            id=data.get("id", ""),
            task=data.get("task", ""),
            goto=[GotoRule.from_dict(g) for g in data.get("goto", [])],
        )


@dataclass
class Lane:
    id: str = ""
    name: str = ""
    steps: list[Step] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Lane:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            steps=[Step.from_dict(s) for s in data.get("steps", [])],
        )


@dataclass
class Phase:
    id: str = ""
    name: str = ""
    steps: list[Step] = field(default_factory=list)  # sequential mode
    lanes: list[Lane] = field(default_factory=list)  # parallel mode, mutually exclusive with steps
    max_iterations: int = 0  # 0 = inherit from workflow
    goto: list[GotoRule] = field(default_factory=list)  # evaluated after all steps/lanes done

    @classmethod
    def from_dict(cls, data: dict) -> Phase:
        lanes_data = data.get("lanes")
        if lanes_data:
            lanes = [Lane.from_dict(l) for l in lanes_data]
            steps = []
        else:
            lanes = []
            steps = [Step.from_dict(s) for s in data.get("steps", [])]

        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            steps=steps,
            lanes=lanes,
            max_iterations=data.get("max_iterations", 0),
            goto=[GotoRule.from_dict(g) for g in data.get("goto", [])],
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
    lane: str | None = None


# ── Template filling ─────────────────────────────────────────

_RE_PLACEHOLDER = re.compile(r"\{(\w+(?:\.\w+)*)\}")


def fill_template(template: str, context: dict) -> str:
    """Replace {a.b.c} placeholders by dot-path lookup in context dict.

    Supports: {args.key}, {env.VAR}, {previous}, {steps.id.output},
    {lane.id.output}, {phase.id.output}.
    """

    # Singular → plural mappings for template aliases
    _BUCKET_ALIASES = {
        "lane": "lanes",
        "phase": "phases",
        "step": "steps",
    }

    def _replace(m: re.Match) -> str:
        path = m.group(1)
        if path == "previous":
            prev = context.get("previous")
            return str(prev) if prev is not None else m.group(0)
        parts = path.split(".", 1)
        if len(parts) == 2:
            bucket, rest = parts
            # Try exact bucket name first, then alias
            obj = context.get(bucket) or context.get(_BUCKET_ALIASES.get(bucket, ""))
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
    description: str = ""
    phases: list[Phase] = field(default_factory=list)
    path: Path | None = None
    max_iterations: int = 100  # global phase entry count limit

    # Runtime state (written by runner)
    status: str = "idle"  # idle | running | done | error | loop_limit
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
            max_iterations=data.get("max_iterations", 100),
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
        count = 0
        for p in self.phases:
            if p.lanes:
                count += sum(len(l.steps) for l in p.lanes)
            else:
                count += len(p.steps)
        return count

    def completed_steps(self) -> int:
        return len(self.results)

    def progress_bar(self) -> str:
        pi = self.phase_index + 1
        tp = self.total_phases
        return f"Phase {pi}/{tp}"

    def summary(self) -> str:
        lines = [f"Workflow: {self.name}", f"Status: {self.status}"]
        for r in self.results:
            phase = self.phases[r.phase_index]
            status = "OK" if r.exit_code == 0 else "FAIL"
            task_preview = r.task[:40] if r.task else "(empty)"
            lines.append(
                f"  [{status}] Phase '{phase.name}' · {task_preview}: {r.duration:.1f}s"
            )
        return "\n".join(lines)

    def detailed_summary(self, max_lines: int = 10) -> str:
        lines = [f"Workflow: {self.name}", f"Status: {self.status}"]
        for r in self.results:
            phase = self.phases[r.phase_index]
            status = "OK" if r.exit_code == 0 else "FAIL"
            task_preview = r.task[:60] if r.task else "(empty)"
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

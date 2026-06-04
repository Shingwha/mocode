"""Workflow data models — pure definitions, no runtime state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ── Data models ──────────────────────────────────────────────


@dataclass
class Route:
    """A single route rule in a Router node.

    ``match`` is a regex tested against the concatenated output of all
    dependency nodes.  ``None`` means unconditional fallback (always matches).
    ``to`` lists the target node IDs to activate when this route fires.
    ``max`` limits how many times this route can fire (0 = unlimited).
    """

    match: str | None = None
    to: list[str] = field(default_factory=list)
    max: int = 0  # 0 = unlimited

    @classmethod
    def from_dict(cls, data: dict) -> Route:
        to_raw = data.get("to", [])
        if isinstance(to_raw, str):
            to_raw = [to_raw]
        return cls(
            match=data.get("match"),
            to=list(to_raw),
            max=data.get("max", 0),
        )


@dataclass
class Node:
    """A node in the workflow graph.

    type "task"  — has a ``task`` template, runs via mocode -p.
    type "router" — has ``routes``, no ``task``; evaluates conditions.

    ``depends`` is auto-inferred from ``{nodes.<id>.*}`` references in the
    ``task`` template and merged with any explicit ``depends``.
    """

    id: str = ""
    type: str = "task"  # "task" | "router"
    description: str = ""  # brief human-readable label
    task: str = ""  # template string (empty for router)
    depends: list[str] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Auto-infer depends from {nodes.X.*} refs, merged with explicit depends."""
        if self.task and self.type == "task":
            inferred = infer_depends_from_task(self.task)
            if inferred:
                seen = set(self.depends)
                self.depends.extend(nid for nid in inferred if nid not in seen)

    @classmethod
    def from_dict(cls, data: dict) -> Node:
        routes_raw = data.get("routes", [])
        return cls(
            id=data.get("id", ""),
            type=data.get("type", "task"),
            description=data.get("description", ""),
            task=data.get("task", ""),
            depends=list(data.get("depends", [])),
            routes=[Route.from_dict(r) for r in routes_raw],
        )


@dataclass
class NodeResult:
    """Execution result for a single node."""

    node_id: str
    task: str
    output: str
    exit_code: int
    duration: float
    error: str | None = None
    status: str = "done"  # "done" | "skipped"
    iteration: int = 1  # which execution (increments on loop)


# ── Depends inference ────────────────────────────────────────

_RE_NODE_REF = re.compile(r"\{node(?:s?)\.(\w+)\.\w+\}")


def infer_depends_from_task(task: str) -> list[str]:
    """Extract node IDs from ``{nodes.<id>.*}`` / ``{node.<id>.*}`` references.

    Returns a sorted unique list of node IDs found in the task template.
    Router nodes (no task) return an empty list.
    """
    return sorted({m.group(1) for m in _RE_NODE_REF.finditer(task)})


# ── Template filling ─────────────────────────────────────────

_RE_PLACEHOLDER = re.compile(r"\{(\w+(?:\.\w+)*)\}")

_BUCKET_ALIASES = {
    "node": "nodes",
}


def fill_template(template: str, context: dict) -> str:
    """Replace {a.b.c} placeholders by dot-path lookup in context dict.

    Supports: {args.key}, {env.VAR}, {previous}, {nodes.id.output},
    {nodes.id.exit_code}, {nodes.id.error}, {nodes.id.duration}.
    Also accepts {node.id.*} as alias for {nodes.id.*}.
    """

    def _replace(m: re.Match) -> str:
        path = m.group(1)
        if path == "previous":
            prev = context.get("previous")
            return str(prev) if prev is not None else m.group(0)
        parts = path.split(".", 1)
        if len(parts) == 2:
            bucket, rest = parts
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
    """A workflow DAG definition — naming + topology, no runtime state."""

    name: str
    description: str = ""
    nodes: list[Node] = field(default_factory=list)
    path: Path | None = None
    max_iterations: int = 100

    def __post_init__(self) -> None:
        # Pre-compute and cache graph lookups (nodes list is immutable after construction)
        self._node_map: dict[str, Node] = {n.id: n for n in self.nodes}
        self._dependents: dict[str, list[str]] = self._build_dependents()

    def _build_dependents(self) -> dict[str, list[str]]:
        dep_map: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for n in self.nodes:
            for dep in n.depends:
                if dep in dep_map:
                    dep_map[dep].append(n.id)
        return dep_map

    @classmethod
    def from_yaml(cls, path: Path) -> Workflow:
        """Parse a workflow YAML file."""
        import yaml

        from .graph import validate_workflow

        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}

        if "phases" in data:
            raise ValueError(
                "Old 'phases' format detected. "
                "Please migrate to the new 'nodes' format. "
                "See documentation for details."
            )

        nodes_data = data.get("nodes", [])
        nodes = [Node.from_dict(n) for n in nodes_data]

        wf = cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            nodes=nodes,
            path=path,
            max_iterations=data.get("max_iterations", 100),
        )

        validate_workflow(nodes, wf._node_map)
        return wf

    # ── Graph query methods ──────────────────────────────────

    @property
    def node_map(self) -> dict[str, Node]:
        return self._node_map

    @property
    def root_nodes(self) -> list[Node]:
        """Nodes with no depends."""
        return [n for n in self.nodes if not n.depends]

    @property
    def dependents(self) -> dict[str, list[str]]:
        """Reverse adjacency: node_id → list of node IDs that depend on it."""
        return self._dependents

    def total_nodes(self) -> int:
        return len(self.nodes)


# ── Summary helpers (standalone, operate on results lists) ──


def summarize(name: str, results: list[NodeResult]) -> str:
    """Compact one-line-per-result summary."""
    lines = [f"Workflow: {name}"]
    for r in results:
        status = "OK" if r.exit_code == 0 else "FAIL"
        task_preview = r.task[:40] if r.task else "(empty)"
        iter_suffix = f" (iter {r.iteration})" if r.iteration > 1 else ""
        lines.append(
            f"  [{status}] {r.node_id}{iter_suffix} · {task_preview}: {r.duration:.1f}s"
        )
    return "\n".join(lines)


def detailed_summarize(name: str, results: list[NodeResult], max_lines: int = 10) -> str:
    """Multi-line summary with output and error excerpts."""
    lines = [f"Workflow: {name}"]
    for r in results:
        status = "OK" if r.exit_code == 0 else "FAIL"
        task_preview = r.task[:60] if r.task else "(empty)"
        iter_suffix = f" (iter {r.iteration})" if r.iteration > 1 else ""
        lines.append(
            f"  [{status}] {r.node_id}{iter_suffix} · {task_preview} ({r.duration:.1f}s)"
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

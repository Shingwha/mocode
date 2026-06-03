"""Workflow engine — DAG-driven multi-node task execution.

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
    """

    id: str = ""
    type: str = "task"  # "task" | "router"
    description: str = ""  # brief human-readable label
    task: str = ""  # template string (empty for router)
    depends: list[str] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)

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


# ── Template filling ─────────────────────────────────────────

_RE_PLACEHOLDER = re.compile(r"\{(\w+(?:\.\w+)*)\}")


def fill_template(template: str, context: dict) -> str:
    """Replace {a.b.c} placeholders by dot-path lookup in context dict.

    Supports: {args.key}, {env.VAR}, {previous}, {nodes.id.output},
    {nodes.id.exit_code}, {nodes.id.error}, {nodes.id.duration}.
    Also accepts {node.id.*} as alias for {nodes.id.*}.
    """

    _BUCKET_ALIASES = {
        "node": "nodes",
    }

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


# ── Graph validation helpers ─────────────────────────────────


def _validate_workflow(nodes: list[Node], node_map: dict[str, Node]) -> None:
    """Raise ValueError on invalid graph structure."""

    # IDs must be unique and non-empty
    seen_ids: set[str] = set()
    for n in nodes:
        if not n.id:
            raise ValueError("All nodes must have a non-empty 'id'")
        if n.id in seen_ids:
            raise ValueError(f"Duplicate node id: '{n.id}'")
        seen_ids.add(n.id)

    # All depends IDs must exist
    for n in nodes:
        for dep in n.depends:
            if dep not in node_map:
                raise ValueError(
                    f"Node '{n.id}' depends on unknown node '{dep}'"
                )

    # All route.to IDs must exist
    for n in nodes:
        if n.type == "router":
            if not n.routes:
                raise ValueError(f"Router node '{n.id}' must have 'routes'")
            if n.task:
                raise ValueError(f"Router node '{n.id}' must not have 'task'")
            for route in n.routes:
                for target in route.to:
                    if target not in node_map:
                        raise ValueError(
                            f"Router '{n.id}' route targets unknown node '{target}'"
                        )
        else:
            if not n.task:
                raise ValueError(f"Task node '{n.id}' must have 'task'")

    # No self-dependency
    for n in nodes:
        if n.id in n.depends:
            raise ValueError(f"Node '{n.id}' cannot depend on itself")

    # Cycle detection — only back-edges from router routes are allowed
    _check_cycles(nodes, node_map)


def _check_cycles(nodes: list[Node], node_map: dict[str, Node]) -> None:
    """Detect cycles. Only back-edges from router route.to are allowed,
    and each cycle containing a back-edge must have at least one max > 0."""

    # Collect all edges: (from_id, to_id, is_back_edge_allowed)
    # Regular edges: from depends → to = depends on
    #   So edge is depends_id → node.id
    # Router route edges: node.id → route.to_target

    # Build adjacency list for the full graph
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}

    # Edges from depends: dependency → dependent
    for n in nodes:
        for dep in n.depends:
            adj[dep].append(n.id)

    # Edges from router routes: router → target
    back_edges: set[tuple[str, str]] = set()
    for n in nodes:
        if n.type == "router":
            for route in n.routes:
                for target in route.to:
                    adj[n.id].append(target)
                    # A route.to that points to an upstream node (in terms of
                    # depends) is a back-edge. We identify it by checking if
                    # target can reach n via depends-only edges.
                    back_edges.add((n.id, target))

    # DFS to detect all cycles and verify back-edge constraints
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n.id: WHITE for n in nodes}
    in_cycle: set[str] = set()

    def dfs(u: str, path: list[str]) -> None:
        color[u] = GRAY
        path.append(u)
        for v in adj[u]:
            if color[v] == GRAY:
                # Found a cycle — collect nodes in cycle
                cycle_start = path.index(v)
                cycle_nodes = path[cycle_start:]
                in_cycle.update(cycle_nodes)

                # Check: every cycle must involve at least one back-edge
                # from a router route with max > 0
                has_valid_back_edge = False
                for i, cn in enumerate(cycle_nodes):
                    nn = cycle_nodes[(i + 1) % len(cycle_nodes)]
                    if (cn, nn) in back_edges:
                        router = node_map.get(cn)
                        if router and router.type == "router":
                            for route in router.routes:
                                if nn in route.to and route.max > 0:
                                    has_valid_back_edge = True
                                    break
                if not has_valid_back_edge:
                    raise ValueError(
                        f"Cycle detected: {' → '.join(cycle_nodes)} → {v}. "
                        f"Only router back-edges with max > 0 are allowed."
                    )
            elif color[v] == WHITE:
                dfs(v, path)
        path.pop()
        color[u] = BLACK

    for n in nodes:
        if color[n.id] == WHITE:
            dfs(n.id, [])


# ── Wave computation ─────────────────────────────────────────


def compute_waves(workflow: Workflow) -> list[list[Node]]:
    """Topological-sort layers (waves). Returns [[wave1_nodes], [wave2_nodes], ...].

    Back-edges (router route.to pointing to upstream) are temporarily removed
    before computing.
    """
    node_map = workflow.node_map

    # Identify back-edge targets: a route.to that appears in the depends
    # chain upstream of the router. Simplification: a route.to that points
    # to a node whose topological level would be <= the router's level.
    # We use a simpler heuristic: remove all edges where route.to points
    # to a node that the router depends on (directly or transitively).
    router_back_targets: set[tuple[str, str]] = set()
    for n in workflow.nodes:
        if n.type == "router":
            # Collect all ancestors of this router via depends
            ancestors = _collect_ancestors(n.id, node_map)
            for route in n.routes:
                for target in route.to:
                    if target in ancestors:
                        router_back_targets.add((n.id, target))

    # Build in-degree map excluding back-edges
    in_degree: dict[str, int] = {n.id: 0 for n in workflow.nodes}
    children: dict[str, list[str]] = {n.id: [] for n in workflow.nodes}

    # Regular depends edges: dep → node
    for n in workflow.nodes:
        for dep in n.depends:
            children[dep].append(n.id)
            in_degree[n.id] += 1

    # Router route edges: router → target (excluding back-edges)
    for n in workflow.nodes:
        if n.type == "router":
            for route in n.routes:
                for target in route.to:
                    if (n.id, target) not in router_back_targets:
                        children[n.id].append(target)
                        in_degree[target] += 1

    # Kahn's BFS with level tracking
    waves: list[list[Node]] = []
    queue: list[str] = [nid for nid, deg in in_degree.items() if deg == 0]
    processed = 0

    while queue:
        # All nodes in current queue are at the same wave level
        wave = [node_map[nid] for nid in queue]
        waves.append(wave)
        processed += len(queue)

        next_queue: list[str] = []
        for nid in queue:
            for child in children[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_queue.append(child)
        queue = next_queue

    # If not all nodes processed, there's a cycle (should have been caught
    # by validation, but handle gracefully)
    if processed < len(workflow.nodes):
        remaining = [node_map[nid] for nid, deg in in_degree.items() if deg > 0]
        waves.append(remaining)

    return waves


def _collect_ancestors(node_id: str, node_map: dict[str, Node]) -> set[str]:
    """Collect all ancestor node IDs via depends edges."""
    visited: set[str] = set()
    stack = list(node_map[node_id].depends)
    while stack:
        nid = stack.pop()
        if nid in visited:
            continue
        visited.add(nid)
        if nid in node_map:
            stack.extend(node_map[nid].depends)
    return visited


# ── Workflow ─────────────────────────────────────────────────


@dataclass
class Workflow:
    name: str
    description: str = ""
    nodes: list[Node] = field(default_factory=list)
    path: Path | None = None
    max_iterations: int = 100

    # Runtime state
    status: str = "idle"  # idle | running | done | error | loop_limit
    results: list[NodeResult] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> Workflow:
        """Parse a workflow YAML file."""
        import yaml

        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}

        # Reject old format
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

        # Validate graph
        _validate_workflow(nodes, wf.node_map)
        return wf

    # ── Graph query methods ──────────────────────────────────

    @property
    def node_map(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    @property
    def root_nodes(self) -> list[Node]:
        """Nodes with no depends."""
        return [n for n in self.nodes if not n.depends]

    @property
    def dependents(self) -> dict[str, list[str]]:
        """Reverse adjacency: node_id → list of node IDs that depend on it."""
        dep_map: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for n in self.nodes:
            for dep in n.depends:
                dep_map[dep].append(n.id)
        return dep_map

    def total_nodes(self) -> int:
        return len(self.nodes)

    def completed_nodes(self) -> int:
        return len(self.results)

    def summary(self) -> str:
        lines = [f"Workflow: {self.name}", f"Status: {self.status}"]
        for r in self.results:
            status = "OK" if r.exit_code == 0 else "FAIL"
            task_preview = r.task[:40] if r.task else "(empty)"
            iter_suffix = f" (iter {r.iteration})" if r.iteration > 1 else ""
            lines.append(
                f"  [{status}] {r.node_id}{iter_suffix} · {task_preview}: {r.duration:.1f}s"
            )
        return "\n".join(lines)

    def detailed_summary(self, max_lines: int = 10) -> str:
        lines = [f"Workflow: {self.name}", f"Status: {self.status}"]
        for r in self.results:
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

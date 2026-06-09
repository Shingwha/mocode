"""Workflow data models — pure definitions, no runtime state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    type "task"   — has a ``task`` template, runs via AgentLoop.
                  With ``each`` + ``as_``: fans out to N child tasks.
    type "router" — has ``routes``, no ``task``; evaluates conditions.

    ``depends`` is auto-inferred from ``{nodes.<id>.*}`` references in the
    ``task`` and ``each`` templates, merged with any explicit ``depends``.
    """

    id: str = ""
    type: str = "task"  # "task" | "router"
    description: str = ""  # brief human-readable label
    task: str = ""  # template string (empty for router)
    depends: list[str] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    # task+each fields (fan-out mode)
    each: str = ""  # list source expression (empty = no fan-out)
    as_: str = ""  # iteration variable name (required when each is set)

    def __post_init__(self) -> None:
        """Auto-infer depends from {nodes.X.*} refs, merged with explicit depends."""
        templates = []
        if self.task and self.type in ("task", "router"):
            templates.append(self.task)
        if self.each:
            templates.append(self.each)
        if templates:
            combined = "\n".join(templates)
            inferred = infer_depends_from_task(combined)
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
            each=data.get("each", ""),
            as_=data.get("as", ""),
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
    sections: dict[str, list[str]] = field(default_factory=dict)  # [TAG] parsed sections
    tool_calls: int = 0  # total tool invocations within this node
    prompt_tokens: int = 0  # cumulative prompt tokens
    completion_tokens: int = 0  # cumulative completion tokens


# ── [TAG] section parsing ───────────────────────────────────

_RE_TAG = re.compile(r'^\[([a-zA-Z_]\w*)\]\s*$', re.MULTILINE)


def parse_sections(output: str) -> dict[str, list[str]]:
    """Parse ``[TAG]`` section markers from node output.

    Tags must appear at the start of a line. Content between tags (or EOF)
    is stripped and grouped by tag name. Same-name tags are merged into a list.
    """
    sections: dict[str, list[str]] = {}
    for m in _RE_TAG.finditer(output):
        tag = m.group(1)
        start = m.end()
        next_m = _RE_TAG.search(output, start)
        end = next_m.start() if next_m else len(output)
        content = output[start:end].strip()
        if content:
            sections.setdefault(tag, []).append(content)
    return sections


# ── Depends inference ────────────────────────────────────────

_RE_NODE_REF = re.compile(r"\{node(?:s?)\.(\w+)\.\w+(?:\[\d+\])?\}")


def infer_depends_from_task(task: str) -> list[str]:
    """Extract node IDs from ``{nodes.<id>.*}`` / ``{node.<id>.*}`` references.

    Returns a sorted unique list of node IDs found in the task template.
    Router nodes (no task) return an empty list.
    """
    return sorted({m.group(1) for m in _RE_NODE_REF.finditer(task)})


# ── Template filling ─────────────────────────────────────────

_RE_PLACEHOLDER = re.compile(r"\{([^{}[\]]+(?:\[\d+\])?)\}")

_BUCKET_ALIASES = {
    "node": "nodes",
}


def fill_template(template: str, context: dict) -> str:
    """Replace {a.b.c} / {a.b.c[0]} placeholders by dot-path lookup in context dict.

    Single-segment paths (``{direction}``, ``{previous}``, ``{item}``) look up
    directly in the context top level.

    Multi-segment paths (``{env.VAR}``, ``{nodes.id.output}``, ``{nodes.id.TAG[0]}``)
    walk into nested dicts. ``{node.id.*}`` is accepted as alias for ``{nodes.id.*}``.
    List values are joined with ``\\n---\\n``.
    """

    def _replace(m: re.Match) -> str:
        path = m.group(1)
        parts = path.split(".", 1)
        if len(parts) == 1:
            # Single segment: {direction}, {previous}, {item}
            val = context.get(path)
            return str(val) if val is not None else m.group(0)
        # Multi-segment: {env.HOME}, {nodes.scan.output}, {nodes.scan.ISSUE[0]}
        bucket, rest = parts
        obj = context.get(bucket) or context.get(_BUCKET_ALIASES.get(bucket, ""))
        if isinstance(obj, dict):
            val = _resolve_path(rest, obj)
            if val is None:
                return m.group(0)
            if isinstance(val, list):
                return "\n---\n".join(str(v) for v in val)
            return str(val)
        return m.group(0)

    return _RE_PLACEHOLDER.sub(_replace, template)


def _resolve_path(path: str, obj: dict) -> Any:
    """Walk 'a.b.c' / 'a.b.c[0]' path into nested dicts. Return None on failure."""
    for part in path.split("."):
        if obj is None:
            return None
        idx_match = re.match(r'^(\w+)\[(\d+)\]$', part)
        if idx_match:
            key, idx = idx_match.group(1), int(idx_match.group(2))
            obj = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(obj, list) and idx < len(obj):
                obj = obj[idx]
            else:
                return None
        else:
            obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


# ── Params parsing ────────────────────────────────────────────


@dataclass
class ParamDef:
    """A workflow parameter definition — name, optional default, and required flag."""

    name: str
    default: str | None = None
    required: bool = True


def _parse_params(raw: list) -> list[ParamDef]:
    """Parse params from YAML data.

    Supports two formats:
      - Pure string: ``direction`` → required, no default
      - Dict: ``{name: depth, default: "deep"}`` or ``{depth: "deep"}`` → optional with default
    """
    params: list[ParamDef] = []
    for item in raw:
        if isinstance(item, str):
            params.append(ParamDef(name=item, default=None, required=True))
        elif isinstance(item, dict):
            if "name" in item:
                # Explicit dict: {name: depth, default: "deep"}
                params.append(ParamDef(
                    name=item["name"],
                    default=item.get("default"),
                    required="default" not in item,
                ))
            else:
                # Shorthand: {depth: "deep"} → name=depth, default="deep"
                for k, v in item.items():
                    params.append(ParamDef(name=k, default=v, required=False))
    return params


def parse_args(params: list[ParamDef], raw_args: list[str]) -> dict[str, str]:
    """Parse positional + key=value arguments against a param definition.

    Positional args are mapped in order. ``key=value`` args override by name.
    Raises ValueError if required params are missing.
    """
    result: dict[str, str] = {}
    positional_idx = 0
    for arg in raw_args:
        if "=" in arg:
            k, v = arg.split("=", 1)
            result[k.strip()] = v.strip()
        else:
            if positional_idx < len(params):
                result[params[positional_idx].name] = arg
                positional_idx += 1
            else:
                # Extra positional — ignore or could raise
                pass

    # Apply defaults for params not yet set
    for p in params:
        if p.name not in result:
            if p.default is not None:
                result[p.name] = p.default
            elif p.required:
                raise ValueError(f"Missing required parameter: {p.name}")

    return result


# ── Items parsing (for map nodes) ─────────────────────────────


def parse_items(raw: str) -> list[str]:
    """Split raw text into non-empty stripped lines."""
    return [line.strip() for line in raw.splitlines() if line.strip()]


def resolve_list_expr(each_expr: str, context: dict) -> list[str]:
    """Resolve an ``each`` expression to a list of strings.

    Priority:
    1. Direct list resolution (e.g. ``{nodes.scan.ISSUE}`` → already a list)
    2. ``fill_template`` + line splitting (covers string outputs and mixed templates)
    """
    expr = each_expr.strip()
    if expr.startswith("{") and expr.endswith("}"):
        inner = expr[1:-1]
        parts = inner.split(".", 1)
        if len(parts) > 1:
            bucket, rest = parts
            obj = context.get(bucket) or context.get(_BUCKET_ALIASES.get(bucket, ""))
            if isinstance(obj, dict):
                val = _resolve_path(rest, obj)
                if isinstance(val, list):
                    return [str(v) for v in val]
        else:
            # Single-segment placeholder
            val = context.get(inner)
            if isinstance(val, list):
                return [str(v) for v in val]
    # Fallback: fill template + split by lines
    filled = fill_template(each_expr, context)
    return [line.strip() for line in filled.splitlines() if line.strip()]


# ── Workflow ─────────────────────────────────────────────────


@dataclass
class Workflow:
    """A workflow DAG definition — naming + topology, no runtime state."""

    name: str
    description: str = ""
    nodes: list[Node] = field(default_factory=list)
    params: list[ParamDef] = field(default_factory=list)
    path: Path | None = None
    max_iterations: int = 100
    concurrency: int = 1  # max parallel node execution (1 = serial)
    timeout: int = 1800   # per-node timeout in seconds (30 min default)

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
        params = _parse_params(data.get("params", []))

        wf = cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            nodes=nodes,
            params=params,
            path=path,
            max_iterations=data.get("max_iterations", 100),
            concurrency=data.get("concurrency", 1),
            timeout=data.get("timeout", 1800),
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

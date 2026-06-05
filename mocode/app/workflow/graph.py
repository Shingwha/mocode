"""Graph validation and wave computation for workflow DAGs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Node, Workflow


# ── Graph validation ─────────────────────────────────────────


def validate_workflow(nodes: list[Node], node_map: dict[str, Node]) -> None:
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
                raise ValueError(f"Node '{n.id}' depends on unknown node '{dep}'")

    # All route.to IDs must exist + node-type-specific validation
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
        elif n.type == "map":
            if not n.items:
                raise ValueError(f"Map node '{n.id}' must have 'items'")
            if not n.task:
                raise ValueError(f"Map node '{n.id}' must have 'task'")
            if n.routes:
                raise ValueError(f"Map node '{n.id}' must not have 'routes'")
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
    """Detect cycles. Only back-edges from router route.to with max > 0 are allowed."""

    adj: dict[str, list[str]] = {n.id: [] for n in nodes}
    for n in nodes:
        for dep in n.depends:
            adj[dep].append(n.id)

    back_edges: set[tuple[str, str]] = set()
    for n in nodes:
        if n.type == "router":
            for route in n.routes:
                for target in route.to:
                    adj[n.id].append(target)
                    back_edges.add((n.id, target))

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n.id: WHITE for n in nodes}

    def dfs(u: str, path: list[str]) -> None:
        color[u] = GRAY
        path.append(u)
        for v in adj[u]:
            if color[v] == GRAY:
                cycle_start = path.index(v)
                cycle_nodes = path[cycle_start:]
                has_valid = any(
                    (cn, nn) in back_edges
                    and node_map.get(cn)
                    and node_map[cn].type == "router"
                    and any(nn in r.to and r.max > 0 for r in node_map[cn].routes)
                    for i, (cn, nn) in enumerate(
                        (
                            (cycle_nodes[i], cycle_nodes[(i + 1) % len(cycle_nodes)])
                            for i in range(len(cycle_nodes))
                        )
                    )
                )
                if not has_valid:
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

    router_back_targets: set[tuple[str, str]] = set()
    for n in workflow.nodes:
        if n.type == "router":
            ancestors = collect_ancestors(n.id, node_map)
            for route in n.routes:
                for target in route.to:
                    if target in ancestors:
                        router_back_targets.add((n.id, target))

    in_degree: dict[str, int] = {n.id: 0 for n in workflow.nodes}
    children: dict[str, list[str]] = {n.id: [] for n in workflow.nodes}

    for n in workflow.nodes:
        for dep in n.depends:
            children[dep].append(n.id)
            in_degree[n.id] += 1

    for n in workflow.nodes:
        if n.type == "router":
            for route in n.routes:
                for target in route.to:
                    if (n.id, target) not in router_back_targets:
                        children[n.id].append(target)
                        in_degree[target] += 1

    waves: list[list[Node]] = []
    queue: list[str] = [nid for nid, deg in in_degree.items() if deg == 0]

    while queue:
        wave = [node_map[nid] for nid in queue]
        waves.append(wave)
        next_queue: list[str] = []
        for nid in queue:
            for child in children[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_queue.append(child)
        queue = next_queue

    return waves


def collect_ancestors(node_id: str, node_map: dict[str, Node]) -> set[str]:
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

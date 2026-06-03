"""Graph validation and wave computation for workflow DAGs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Node, Workflow


# ── Graph validation ─────────────────────────────────────────


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
                    back_edges.add((n.id, target))

    # DFS to detect all cycles and verify back-edge constraints
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n.id: WHITE for n in nodes}

    def dfs(u: str, path: list[str]) -> None:
        color[u] = GRAY
        path.append(u)
        for v in adj[u]:
            if color[v] == GRAY:
                # Found a cycle — collect nodes in cycle
                cycle_start = path.index(v)
                cycle_nodes = path[cycle_start:]

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
    # chain upstream of the router.
    router_back_targets: set[tuple[str, str]] = set()
    for n in workflow.nodes:
        if n.type == "router":
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

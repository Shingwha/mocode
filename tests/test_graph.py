"""Tests for graph validation helpers."""

from __future__ import annotations

import pytest

from mocode.app.workflow.graph import (
    _validate_task_node,
    _validate_router_node,
    _validate_task_each_node,
    validate_workflow,
)
from mocode.app.workflow.models import Node, Route


# ── Helper to build node_map ─────────────────────────────────


def _node_map(*nodes: Node) -> dict[str, Node]:
    return {n.id: n for n in nodes}


# ── _validate_task_node ──────────────────────────────────────


class TestValidateTaskNode:
    def test_valid_task_node(self):
        node = Node(id="t1", type="task", task="do something")
        errors: list[str] = []
        _validate_task_node(node, errors)
        assert errors == []

    def test_missing_task(self):
        node = Node(id="t1", type="task", task="")
        errors: list[str] = []
        _validate_task_node(node, errors)
        assert len(errors) == 1
        assert "must have 'task'" in errors[0]


# ── _validate_router_node ────────────────────────────────────


class TestValidateRouterNode:
    def test_valid_router_node(self):
        node = Node(
            id="r1",
            type="router",
            routes=[Route(to=["a"])],
        )
        nmap = _node_map(node, Node(id="a", type="task", task="x"))
        errors: list[str] = []
        _validate_router_node(node, nmap, errors)
        assert errors == []

    def test_missing_routes(self):
        node = Node(id="r1", type="router", routes=[])
        errors: list[str] = []
        _validate_router_node(node, {}, errors)
        assert any("must have 'routes'" in e for e in errors)

    def test_router_has_task(self):
        node = Node(
            id="r1",
            type="router",
            task="should not have this",
            routes=[Route(to=["a"])],
        )
        nmap = _node_map(node, Node(id="a", type="task", task="x"))
        errors: list[str] = []
        _validate_router_node(node, nmap, errors)
        assert any("must not have 'task'" in e for e in errors)

    def test_unknown_route_target(self):
        node = Node(
            id="r1",
            type="router",
            routes=[Route(to=["nonexistent"])],
        )
        errors: list[str] = []
        _validate_router_node(node, {}, errors)
        assert any("unknown node 'nonexistent'" in e for e in errors)

    def test_multiple_errors_collected(self):
        node = Node(
            id="r1",
            type="router",
            task="bad",
            routes=[Route(to=["missing"])],
        )
        errors: list[str] = []
        _validate_router_node(node, {}, errors)
        assert len(errors) == 2  # has task + unknown target


# ── _validate_map_node ───────────────────────────────────────


class TestValidateTaskEachNode:
    def test_valid_task_each_node(self):
        node = Node(id="m1", each="a\nb", as_="item", task="process {item}")
        errors: list[str] = []
        _validate_task_each_node(node, errors)
        assert errors == []

    def test_missing_each(self):
        node = Node(id="m1", each="", as_="item", task="x")
        errors: list[str] = []
        _validate_task_each_node(node, errors)
        assert any("must have 'each'" in e for e in errors)

    def test_missing_task(self):
        node = Node(id="m1", each="a", as_="item", task="")
        errors: list[str] = []
        _validate_task_each_node(node, errors)
        assert any("must have 'task'" in e for e in errors)

    def test_missing_as(self):
        node = Node(id="m1", each="a", as_="", task="x")
        errors: list[str] = []
        _validate_task_each_node(node, errors)
        assert any("must have 'as'" in e for e in errors)

    def test_multiple_errors_collected(self):
        node = Node(id="m1", each="", as_="", task="")
        errors: list[str] = []
        _validate_task_each_node(node, errors)
        assert len(errors) == 3  # missing each, missing task, missing as


# ── validate_workflow (integration) ──────────────────────────


class TestValidateWorkflow:
    def test_valid_workflow(self):
        nodes = [
            Node(id="a", type="task", task="do a"),
            Node(id="b", type="task", task="do b", depends=["a"]),
        ]
        nmap = _node_map(*nodes)
        # Should not raise
        validate_workflow(nodes, nmap)

    def test_duplicate_ids(self):
        nodes = [
            Node(id="a", type="task", task="x"),
            Node(id="a", type="task", task="y"),
        ]
        nmap = _node_map(nodes[0])
        with pytest.raises(ValueError, match="Duplicate node id"):
            validate_workflow(nodes, nmap)

    def test_unknown_dependency(self):
        nodes = [Node(id="a", type="task", task="x", depends=["missing"])]
        nmap = _node_map(nodes[0])
        with pytest.raises(ValueError, match="unknown node 'missing'"):
            validate_workflow(nodes, nmap)

    def test_collects_multiple_type_errors(self):
        """validate_workflow collects all type errors and raises once."""
        nodes = [
            Node(id="bad_task", type="task", task=""),
            Node(id="bad_each", each="", as_="", task=""),
        ]
        nmap = _node_map(*nodes)
        with pytest.raises(ValueError) as exc_info:
            validate_workflow(nodes, nmap)
        msg = str(exc_info.value)
        assert "bad_task" in msg
        assert "bad_each" in msg

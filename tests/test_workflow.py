"""Tests for workflow engine — models, fill_template, registry, waves, runner, command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.app.workflow import (
    LoopIterEvent,
    Node,
    NodeDoneEvent,
    NodeResult,
    NodeSkippedEvent,
    Route,
    WaveReadyEvent,
    Workflow,
    WorkflowRegistry,
    WorkflowEvent,
    compute_waves,
    fill_template,
)
from mocode.app.workflow.events import (
    ProgressEvent,
    RouterConditionEvent,
)
from mocode.app.workflow.models import infer_depends_from_task
from mocode.app.workflow.runner import DAGRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> Path:
    """Write a YAML workflow file and return its path."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _make_ctx(app=None, display=None, args=""):
    from mocode.app.cli.commands import CommandContext

    return CommandContext(
        app=app or MagicMock(),
        args=args,
        display=display or MagicMock(),
    )


def _make_subprocess_mock(stdout: bytes = b"output", returncode: int = 0):
    """Create a mock for asyncio.create_subprocess_exec return value."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


def _simple_linear_wf(n: int = 3) -> Workflow:
    """Build a linear chain: n0 -> n1 -> ... -> n{n-1}."""
    nodes = [
        Node(id=f"n{i}", task=f"Task {i}", depends=[f"n{i-1}"] if i > 0 else [])
        for i in range(n)
    ]
    return Workflow(name="linear", nodes=nodes)


def _simple_parallel_wf() -> Workflow:
    """Build a fan-out/fan-in diamond: root -> (a, b) -> merge."""
    return Workflow(
        name="diamond",
        nodes=[
            Node(id="root", task="Root"),
            Node(id="a", task="A", depends=["root"]),
            Node(id="b", task="B", depends=["root"]),
            Node(id="merge", task="Merge", depends=["a", "b"]),
        ],
    )


# ===========================================================================
# 1. Model tests — Route, Node, NodeResult, Workflow
# ===========================================================================


class TestRouteModel:
    def test_from_dict_full(self):
        r = Route.from_dict({"match": "error", "to": ["fix"], "max": 3})
        assert r.match == "error"
        assert r.to == ["fix"]
        assert r.max == 3

    def test_from_dict_defaults(self):
        r = Route.from_dict({})
        assert r.match is None
        assert r.to == []
        assert r.max == 0

    def test_from_dict_to_as_string(self):
        r = Route.from_dict({"to": "single"})
        assert r.to == ["single"]

    def test_from_dict_to_as_list(self):
        r = Route.from_dict({"to": ["a", "b"]})
        assert r.to == ["a", "b"]

    def test_from_dict_null_match(self):
        r = Route.from_dict({"match": None, "to": ["done"]})
        assert r.match is None

    def test_default_constructor(self):
        r = Route()
        assert r.match is None
        assert r.to == []
        assert r.max == 0


class TestNodeModel:
    def test_from_dict_task(self):
        n = Node.from_dict({"id": "scan", "task": "Scan code"})
        assert n.id == "scan"
        assert n.type == "task"
        assert n.task == "Scan code"
        assert n.depends == []
        assert n.routes == []

    def test_from_dict_router(self):
        n = Node.from_dict({
            "id": "decide",
            "type": "router",
            "depends": ["summary"],
            "routes": [
                {"match": "critical", "to": ["fix"]},
                {"match": None, "to": ["done"]},
            ],
        })
        assert n.id == "decide"
        assert n.type == "router"
        assert n.task == ""
        assert n.depends == ["summary"]
        assert len(n.routes) == 2
        assert n.routes[0].match == "critical"
        assert n.routes[1].match is None

    def test_from_dict_defaults(self):
        n = Node.from_dict({})
        assert n.id == ""
        assert n.type == "task"
        assert n.task == ""
        assert n.depends == []
        assert n.routes == []

    def test_from_dict_with_depends(self):
        n = Node.from_dict({"id": "x", "task": "X", "depends": ["a", "b"]})
        assert n.depends == ["a", "b"]

    def test_default_constructor(self):
        n = Node()
        assert n.id == ""
        assert n.type == "task"


class TestNodeResultModel:
    def test_basic(self):
        nr = NodeResult(
            node_id="scan", task="Scan", output="found 3 issues",
            exit_code=0, duration=5.2,
        )
        assert nr.node_id == "scan"
        assert nr.output == "found 3 issues"
        assert nr.exit_code == 0
        assert nr.duration == 5.2
        assert nr.error is None
        assert nr.status == "done"
        assert nr.iteration == 1

    def test_with_error(self):
        nr = NodeResult(
            node_id="x", task="", output="", exit_code=1,
            duration=0.5, error="timed out",
        )
        assert nr.error == "timed out"
        assert nr.exit_code == 1

    def test_skipped_status(self):
        nr = NodeResult(
            node_id="x", task="", output="", exit_code=0,
            duration=0, status="skipped",
        )
        assert nr.status == "skipped"

    def test_iteration(self):
        nr = NodeResult(
            node_id="fix", task="Fix", output="fixed",
            exit_code=0, duration=3.0, iteration=3,
        )
        assert nr.iteration == 3


class TestDependsInference:
    """Tests for auto-inference of depends from task template references."""

    def test_infer_from_nodes_output(self):
        assert infer_depends_from_task("Check {nodes.scan.output}") == ["scan"]

    def test_infer_multiple_refs(self):
        assert infer_depends_from_task(
            "A: {nodes.a.output}, B: {nodes.b.duration}"
        ) == ["a", "b"]

    def test_infer_ignores_args(self):
        assert infer_depends_from_task("Hello {args.name}") == []

    def test_infer_ignores_env(self):
        assert infer_depends_from_task("Path: {env.HOME}") == []

    def test_infer_ignores_previous(self):
        assert infer_depends_from_task("Prev: {previous}") == []

    def test_infer_empty_task(self):
        assert infer_depends_from_task("") == []

    def test_infer_node_alias(self):
        assert infer_depends_from_task("{node.x.output}") == ["x"]

    def test_infer_sorted_unique(self):
        result = infer_depends_from_task(
            "{nodes.b.output} {nodes.a.output} {nodes.b.output}"
        )
        assert result == ["a", "b"]

    def test_node_from_dict_auto_infers(self):
        n = Node.from_dict({"id": "t", "task": "Process {nodes.src.output}"})
        assert "src" in n.depends

    def test_node_from_dict_merges_with_explicit(self):
        n = Node.from_dict({
            "id": "t", "task": "Use {nodes.src.output}",
            "depends": ["gate"],
        })
        assert "gate" in n.depends
        assert "src" in n.depends

    def test_node_from_dict_no_task_no_inference(self):
        n = Node.from_dict({"id": "r", "type": "router", "routes": []})
        assert n.depends == []

    def test_node_constructor_auto_infers(self):
        """__post_init__ inference also works when using Node() directly."""
        n = Node(id="t", task="Process {nodes.src.output}")
        assert "src" in n.depends

    def test_node_constructor_merges_explicit(self):
        n = Node(id="t", task="Use {nodes.src.output}", depends=["gate"])
        assert n.depends == ["gate", "src"]


class TestWorkflowModel:
    def test_basic_construction(self):
        wf = Workflow(name="test", description="desc")
        assert wf.name == "test"
        assert wf.description == "desc"
        assert wf.nodes == []
        assert wf.max_iterations == 100
        assert wf.status == "idle"
        assert wf.results == []

    def test_node_map(self):
        wf = Workflow(
            name="test",
            nodes=[Node(id="a", task="A"), Node(id="b", task="B")],
        )
        nm = wf.node_map
        assert set(nm.keys()) == {"a", "b"}
        assert nm["a"].task == "A"

    def test_root_nodes(self):
        wf = Workflow(
            name="test",
            nodes=[
                Node(id="a", task="A"),
                Node(id="b", task="B", depends=["a"]),
            ],
        )
        roots = wf.root_nodes
        assert len(roots) == 1
        assert roots[0].id == "a"

    def test_dependents(self):
        wf = Workflow(
            name="test",
            nodes=[
                Node(id="a", task="A"),
                Node(id="b", task="B", depends=["a"]),
                Node(id="c", task="C", depends=["a"]),
            ],
        )
        dep = wf.dependents
        assert set(dep["a"]) == {"b", "c"}
        assert dep["b"] == []
        assert dep["c"] == []

    def test_total_nodes(self):
        wf = Workflow(name="test", nodes=[Node(id="a"), Node(id="b")])
        assert wf.total_nodes() == 2

    def test_summary(self):
        wf = Workflow(name="test")
        wf.results.append(
            NodeResult(node_id="a", task="Do thing", output="ok", exit_code=0, duration=1.5)
        )
        s = wf.summary()
        assert "test" in s
        assert "[OK]" in s
        assert "Do thing" in s

    def test_summary_failed(self):
        wf = Workflow(name="test")
        wf.results.append(
            NodeResult(node_id="a", task="Fail", output="", exit_code=1, duration=0.5, error="boom")
        )
        s = wf.summary()
        assert "[FAIL]" in s

    def test_detailed_summary_includes_output(self):
        wf = Workflow(name="test")
        wf.results.append(
            NodeResult(node_id="a", task="T", output="Hello world", exit_code=0, duration=1.0)
        )
        s = wf.detailed_summary()
        assert "Hello world" in s

    def test_detailed_summary_truncates_long_output(self):
        wf = Workflow(name="test")
        long_output = "\n".join(f"line {i}" for i in range(50))
        wf.results.append(
            NodeResult(node_id="a", task="T", output=long_output, exit_code=0, duration=1.0)
        )
        s = wf.detailed_summary()
        assert "more lines" in s

    def test_detailed_summary_shows_error(self):
        wf = Workflow(name="test")
        wf.results.append(
            NodeResult(node_id="a", task="T", output="", exit_code=1, duration=0.5, error="kaboom")
        )
        s = wf.detailed_summary()
        assert "kaboom" in s

    def test_summary_with_iteration(self):
        wf = Workflow(name="test")
        wf.results.append(
            NodeResult(node_id="fix", task="Fix", output="ok", exit_code=0, duration=2.0, iteration=3)
        )
        s = wf.summary()
        assert "iter 3" in s


# ===========================================================================
# 2. Validation tests
# ===========================================================================


class TestWorkflowValidation:
    def test_duplicate_id_via_yaml(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "dup.yaml", {
            "name": "dup",
            "nodes": [
                {"id": "a", "task": "A"},
                {"id": "a", "task": "A2"},
            ],
        })
        with pytest.raises(ValueError, match="Duplicate"):
            Workflow.from_yaml(path)

    def test_empty_id_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "empty_id.yaml", {
            "name": "test",
            "nodes": [{"id": "", "task": "A"}],
        })
        with pytest.raises(ValueError, match="non-empty"):
            Workflow.from_yaml(path)

    def test_unknown_dep_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "bad_dep.yaml", {
            "name": "test",
            "nodes": [
                {"id": "a", "task": "A", "depends": ["nonexistent"]},
            ],
        })
        with pytest.raises(ValueError, match="unknown node"):
            Workflow.from_yaml(path)

    def test_unknown_route_target_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "bad_route.yaml", {
            "name": "test",
            "nodes": [
                {"id": "a", "task": "A"},
                {
                    "id": "r",
                    "type": "router",
                    "depends": ["a"],
                    "routes": [{"match": None, "to": ["ghost"]}],
                },
            ],
        })
        with pytest.raises(ValueError, match="unknown node"):
            Workflow.from_yaml(path)

    def test_router_must_have_routes(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "no_routes.yaml", {
            "name": "test",
            "nodes": [
                {"id": "a", "task": "A"},
                {"id": "r", "type": "router", "depends": ["a"]},
            ],
        })
        with pytest.raises(ValueError, match="must have 'routes'"):
            Workflow.from_yaml(path)

    def test_router_must_not_have_task(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "router_task.yaml", {
            "name": "test",
            "nodes": [
                {"id": "a", "task": "A"},
                {
                    "id": "r", "type": "router", "depends": ["a"],
                    "task": "Should not be here",
                    "routes": [{"match": None, "to": ["a"]}],
                },
            ],
        })
        with pytest.raises(ValueError, match="must not have 'task'"):
            Workflow.from_yaml(path)

    def test_task_node_must_have_task(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "no_task.yaml", {
            "name": "test",
            "nodes": [{"id": "a"}],
        })
        with pytest.raises(ValueError, match="must have 'task'"):
            Workflow.from_yaml(path)

    def test_self_dependency_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "self_dep.yaml", {
            "name": "test",
            "nodes": [
                {"id": "a", "task": "A", "depends": ["a"]},
            ],
        })
        with pytest.raises(ValueError, match="depend on itself"):
            Workflow.from_yaml(path)

    def test_old_phases_format_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "old.yaml", {
            "name": "old",
            "phases": [{"name": "P1", "steps": [{"task": "T"}]}],
        })
        with pytest.raises(ValueError, match="Old 'phases' format"):
            Workflow.from_yaml(path)

    def test_cycle_without_router_back_edge_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "cycle.yaml", {
            "name": "test",
            "nodes": [
                {"id": "a", "task": "A", "depends": ["b"]},
                {"id": "b", "task": "B", "depends": ["a"]},
            ],
        })
        with pytest.raises(ValueError, match="Cycle"):
            Workflow.from_yaml(path)

    def test_valid_router_back_edge_accepted(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "loop.yaml", {
            "name": "test",
            "nodes": [
                {"id": "do", "task": "Do work"},
                {
                    "id": "check",
                    "type": "router",
                    "depends": ["do"],
                    "routes": [
                        {"match": "FAIL", "to": ["do"], "max": 3},
                        {"match": None, "to": ["done"]},
                    ],
                },
                {"id": "done", "task": "Done", "depends": ["check"]},
            ],
        })
        wf = Workflow.from_yaml(path)
        assert wf.name == "test"
        assert len(wf.nodes) == 3

    def test_router_back_edge_without_max_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "inf_loop.yaml", {
            "name": "test",
            "nodes": [
                {"id": "do", "task": "Do work"},
                {
                    "id": "check",
                    "type": "router",
                    "depends": ["do"],
                    "routes": [
                        {"match": "FAIL", "to": ["do"]},
                        {"match": None, "to": ["done"]},
                    ],
                },
                {"id": "done", "task": "Done", "depends": ["check"]},
            ],
        })
        with pytest.raises(ValueError, match="Cycle"):
            Workflow.from_yaml(path)


# ===========================================================================
# 3. from_yaml parsing tests
# ===========================================================================


class TestWorkflowFromYaml:
    def test_basic_parse(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "basic.yaml", {
            "name": "basic",
            "description": "A test",
            "nodes": [
                {"id": "a", "task": "Hello"},
                {"id": "b", "task": "World", "depends": ["a"]},
            ],
        })
        wf = Workflow.from_yaml(path)
        assert wf.name == "basic"
        assert wf.description == "A test"
        assert len(wf.nodes) == 2
        assert wf.nodes[1].depends == ["a"]

    def test_missing_name_uses_stem(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "my-workflow.yaml", {
            "nodes": [{"id": "a", "task": "A"}],
        })
        wf = Workflow.from_yaml(path)
        assert wf.name == "my-workflow"

    def test_max_iterations(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "mi.yaml", {
            "name": "mi",
            "max_iterations": 50,
            "nodes": [{"id": "a", "task": "A"}],
        })
        wf = Workflow.from_yaml(path)
        assert wf.max_iterations == 50

    def test_default_max_iterations(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "def.yaml", {
            "name": "def",
            "nodes": [{"id": "a", "task": "A"}],
        })
        wf = Workflow.from_yaml(path)
        assert wf.max_iterations == 100

    def test_path_stored(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "p.yaml", {
            "name": "p",
            "nodes": [{"id": "a", "task": "A"}],
        })
        wf = Workflow.from_yaml(path)
        assert wf.path == path

    def test_router_node_parsed(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "router.yaml", {
            "name": "r",
            "nodes": [
                {"id": "a", "task": "A"},
                {
                    "id": "r1", "type": "router", "depends": ["a"],
                    "routes": [
                        {"match": "ok", "to": ["done"]},
                        {"match": None, "to": ["done"]},
                    ],
                },
                {"id": "done", "task": "Done", "depends": ["r1"]},
            ],
        })
        wf = Workflow.from_yaml(path)
        router = wf.node_map["r1"]
        assert router.type == "router"
        assert len(router.routes) == 2


# ===========================================================================
# 4. Template variable tests
# ===========================================================================


class TestFillTemplate:
    def test_args_placeholder(self):
        ctx = {"args": {"topic": "AI"}}
        assert fill_template("Research {args.topic}", ctx) == "Research AI"

    def test_env_placeholder(self):
        ctx = {"env": {"HOME": "/home/user"}}
        assert fill_template("{env.HOME}", ctx) == "/home/user"

    def test_previous_placeholder(self):
        ctx = {"previous": "last output"}
        assert fill_template("{previous}", ctx) == "last output"

    def test_previous_none_kept(self):
        ctx = {"previous": None}
        assert fill_template("{previous}", ctx) == "{previous}"

    def test_nodes_output(self):
        ctx = {"nodes": {"scan": {"output": "found 5 files"}}}
        assert fill_template("{nodes.scan.output}", ctx) == "found 5 files"

    def test_nodes_exit_code(self):
        ctx = {"nodes": {"scan": {"output": "ok", "exit_code": 0}}}
        assert fill_template("{nodes.scan.exit_code}", ctx) == "0"

    def test_nodes_error(self):
        ctx = {"nodes": {"scan": {"output": "", "error": "timeout"}}}
        assert fill_template("{nodes.scan.error}", ctx) == "timeout"

    def test_nodes_duration(self):
        ctx = {"nodes": {"scan": {"output": "", "duration": 5.2}}}
        assert fill_template("{nodes.scan.duration}", ctx) == "5.2"

    def test_node_alias(self):
        ctx = {"nodes": {"a": {"output": "hello"}}}
        assert fill_template("{node.a.output}", ctx) == "hello"

    def test_unknown_placeholder_kept(self):
        assert fill_template("{unknown.thing}", {}) == "{unknown.thing}"

    def test_mixed_placeholders(self):
        ctx = {
            "args": {"q": "test"},
            "nodes": {"s1": {"output": "result"}},
            "previous": "prev",
        }
        result = fill_template("{args.q} + {nodes.s1.output} + {previous}", ctx)
        assert result == "test + result + prev"

    def test_nested_dot_path_missing(self):
        ctx = {"nodes": {}}
        assert fill_template("{nodes.missing.output}", ctx) == "{nodes.missing.output}"


# ===========================================================================
# 5. Registry tests
# ===========================================================================


class TestWorkflowRegistry:
    def test_discovers_yaml(self, tmp_path: Path):
        _write_yaml(tmp_path / "wf1.yaml", {
            "name": "wf1",
            "nodes": [{"id": "a", "task": "A"}],
        })
        reg = WorkflowRegistry([tmp_path])
        assert reg.names() == ["wf1"]

    def test_discovers_yml_too(self, tmp_path: Path):
        _write_yaml(tmp_path / "wf2.yml", {
            "name": "wf2",
            "nodes": [{"id": "a", "task": "A"}],
        })
        reg = WorkflowRegistry([tmp_path])
        assert "wf2" in reg.names()

    def test_skips_non_yaml(self, tmp_path: Path):
        (tmp_path / "readme.txt").write_text("not yaml", encoding="utf-8")
        reg = WorkflowRegistry([tmp_path])
        assert reg.names() == []

    def test_get(self, tmp_path: Path):
        _write_yaml(tmp_path / "wf.yaml", {
            "name": "my-wf",
            "nodes": [{"id": "a", "task": "A"}],
        })
        reg = WorkflowRegistry([tmp_path])
        assert reg.get("my-wf") is not None
        assert reg.get("nope") is None

    def test_list(self, tmp_path: Path):
        _write_yaml(tmp_path / "a.yaml", {"name": "a", "nodes": [{"id": "x", "task": "X"}]})
        _write_yaml(tmp_path / "b.yaml", {"name": "b", "nodes": [{"id": "y", "task": "Y"}]})
        reg = WorkflowRegistry([tmp_path])
        assert len(reg.list()) == 2

    def test_empty_dir(self, tmp_path: Path):
        reg = WorkflowRegistry([tmp_path])
        assert reg.list() == []

    def test_nonexistent_dir(self, tmp_path: Path):
        reg = WorkflowRegistry([tmp_path / "missing"])
        assert reg.list() == []

    def test_invalid_yaml_skipped(self, tmp_path: Path):
        (tmp_path / "bad.yaml").write_text(":::invalid:::\n  [", encoding="utf-8")
        _write_yaml(tmp_path / "good.yaml", {
            "name": "good",
            "nodes": [{"id": "a", "task": "A"}],
        })
        reg = WorkflowRegistry([tmp_path])
        assert reg.names() == ["good"]

    def test_multiple_dirs(self, tmp_path: Path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        _write_yaml(d1 / "w1.yaml", {"name": "w1", "nodes": [{"id": "x", "task": "X"}]})
        _write_yaml(d2 / "w2.yaml", {"name": "w2", "nodes": [{"id": "y", "task": "Y"}]})
        reg = WorkflowRegistry([d1, d2])
        assert set(reg.names()) == {"w1", "w2"}

    def test_later_dir_overwrites_same_name(self, tmp_path: Path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        _write_yaml(d1 / "shared.yaml", {
            "name": "shared", "description": "v1",
            "nodes": [{"id": "x", "task": "X"}],
        })
        _write_yaml(d2 / "shared.yaml", {
            "name": "shared", "description": "v2",
            "nodes": [{"id": "y", "task": "Y"}],
        })
        reg = WorkflowRegistry([d1, d2])
        assert reg.get("shared").description == "v2"

    def test_invalid_workflow_skipped(self, tmp_path: Path):
        _write_yaml(tmp_path / "bad.yaml", {
            "name": "bad",
            "nodes": [
                {"id": "a", "task": "A"},
                {"id": "a", "task": "A2"},
            ],
        })
        _write_yaml(tmp_path / "ok.yaml", {
            "name": "ok",
            "nodes": [{"id": "b", "task": "B"}],
        })
        reg = WorkflowRegistry([tmp_path])
        assert reg.names() == ["ok"]


# ===========================================================================
# 6. Wave computation tests
# ===========================================================================


class TestComputeWaves:
    def test_single_node(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="A")])
        waves = compute_waves(wf)
        assert len(waves) == 1
        assert [n.id for n in waves[0]] == ["a"]

    def test_linear_chain(self):
        wf = _simple_linear_wf(3)
        waves = compute_waves(wf)
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["n0"]
        assert [n.id for n in waves[1]] == ["n1"]
        assert [n.id for n in waves[2]] == ["n2"]

    def test_parallel_fan_out(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="root", task="R"),
                Node(id="a", task="A", depends=["root"]),
                Node(id="b", task="B", depends=["root"]),
                Node(id="c", task="C", depends=["root"]),
            ],
        )
        waves = compute_waves(wf)
        assert len(waves) == 2
        assert [n.id for n in waves[0]] == ["root"]
        assert set(n.id for n in waves[1]) == {"a", "b", "c"}

    def test_diamond(self):
        wf = _simple_parallel_wf()
        waves = compute_waves(wf)
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["root"]
        assert set(n.id for n in waves[1]) == {"a", "b"}
        assert [n.id for n in waves[2]] == ["merge"]

    def test_with_router_back_edge(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do work"),
                Node(
                    id="check", type="router", depends=["do"],
                    routes=[
                        Route(match="FAIL", to=["do"], max=3),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["check"]),
            ],
        )
        waves = compute_waves(wf)
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["do"]
        assert [n.id for n in waves[1]] == ["check"]
        assert [n.id for n in waves[2]] == ["done"]

    def test_multi_root(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(id="b", task="B"),
                Node(id="c", task="C", depends=["a", "b"]),
            ],
        )
        waves = compute_waves(wf)
        assert len(waves) == 2
        assert set(n.id for n in waves[0]) == {"a", "b"}
        assert [n.id for n in waves[1]] == ["c"]


# ===========================================================================
# 7. Runner — Linear chain
# ===========================================================================


class TestRunnerLinearChain:
    @pytest.mark.asyncio
    async def test_single_node(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Say hello")])
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"hello")
            results = await runner.run()

        assert len(results) == 1
        assert results[0].node_id == "a"
        assert results[0].output == "hello"
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_linear_chain_abc(self):
        wf = _simple_linear_wf(3)
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"out-a"),
                _make_subprocess_mock(b"out-b"),
                _make_subprocess_mock(b"out-c"),
            ]
            results = await runner.run()

        assert len(results) == 3
        assert results[0].output == "out-a"
        assert results[1].output == "out-b"
        assert results[2].output == "out-c"
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_template_filling_in_chain(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="n0", task="First"),
                Node(id="n1", task="Result: {nodes.n0.output}", depends=["n0"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"hello"),
                _make_subprocess_mock(b"world"),
            ]
            results = await runner.run()

        assert results[1].task == "Result: hello"

    @pytest.mark.asyncio
    async def test_args_passed_to_template(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Hello {args.name}")])
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"hi")
            results = await runner.run(args={"name": "World"})

        assert results[0].task == "Hello World"

    @pytest.mark.asyncio
    async def test_previous_in_chain(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="First"),
                Node(id="b", task="Continue: {previous}", depends=["a"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"step-a"),
                _make_subprocess_mock(b"step-b"),
            ]
            results = await runner.run()

        assert "step-a" in results[1].task


# ===========================================================================
# 8. Runner — Parallel execution
# ===========================================================================


class TestRunnerParallel:
    @pytest.mark.asyncio
    async def test_diamond_all_nodes_run(self):
        wf = _simple_parallel_wf()
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"root-out"),
                _make_subprocess_mock(b"a-out"),
                _make_subprocess_mock(b"b-out"),
                _make_subprocess_mock(b"merge-out"),
            ]
            results = await runner.run()

        assert len(results) == 4
        ids = {r.node_id for r in results}
        assert ids == {"root", "a", "b", "merge"}
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_parallel_nodes_use_dependency_output(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="root", task="Root"),
                Node(id="a", task="From root: {nodes.root.output}", depends=["root"]),
                Node(id="b", task="From root: {nodes.root.output}", depends=["root"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"root-data"),
                _make_subprocess_mock(b"a-done"),
                _make_subprocess_mock(b"b-done"),
            ]
            results = await runner.run()

        a_result = next(r for r in results if r.node_id == "a")
        b_result = next(r for r in results if r.node_id == "b")
        assert "root-data" in a_result.task
        assert "root-data" in b_result.task

    @pytest.mark.asyncio
    async def test_independent_nodes_all_run(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(id="b", task="B"),
                Node(id="c", task="C"),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a"),
                _make_subprocess_mock(b"b"),
                _make_subprocess_mock(b"c"),
            ]
            results = await runner.run()

        assert len(results) == 3
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_merge_waits_for_both(self):
        wf = _simple_parallel_wf()
        runner = DAGRunner(wf, node_context=False)
        call_order = []

        async def tracking_exec(*args, **kwargs):
            task_text = args[2] if len(args) > 2 else ""
            proc = MagicMock()
            proc.returncode = 0
            if "Root" in task_text:
                proc.communicate = AsyncMock(return_value=(b"root", b""))
                call_order.append("root")
            elif task_text == "A":
                proc.communicate = AsyncMock(return_value=(b"a", b""))
                call_order.append("a")
            elif task_text == "B":
                proc.communicate = AsyncMock(return_value=(b"b", b""))
                call_order.append("b")
            else:
                proc.communicate = AsyncMock(return_value=(b"merge", b""))
                call_order.append("merge")
            return proc

        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec", side_effect=tracking_exec):
            results = await runner.run()

        assert len(results) == 4
        merge_idx = call_order.index("merge")
        a_idx = call_order.index("a")
        b_idx = call_order.index("b")
        assert merge_idx > a_idx
        assert merge_idx > b_idx


# ===========================================================================
# 9. Runner — Router
# ===========================================================================


class TestRunnerRouter:
    @pytest.mark.asyncio
    async def test_router_matches_first_route(self):
        """Router picks first matching route; non-targets are skipped."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(
                    id="r", type="router", depends=["a"],
                    routes=[
                        Route(match="yes", to=["b"]),
                        Route(match=None, to=["c"]),
                    ],
                ),
                Node(id="b", task="B", depends=["r"]),
                Node(id="c", task="C", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"yes please"),  # a
                _make_subprocess_mock(b"b done"),       # b (activated by route 0)
            ]
            results = await runner.run()

        completed_ids = [r.node_id for r in results]
        assert "b" in completed_ids
        # c was not activated by the matched route, so it's skipped
        assert "c" not in completed_ids

    @pytest.mark.asyncio
    async def test_router_falls_through_to_fallback(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(
                    id="r", type="router", depends=["a"],
                    routes=[
                        Route(match="critical", to=["fix"]),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="fix", task="Fix it", depends=["r"]),
                Node(id="done", task="Report", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"no issues"),      # a
                _make_subprocess_mock(b"report generated"), # done
            ]
            results = await runner.run()

        ids = [r.node_id for r in results]
        assert "done" in ids
        assert "fix" not in ids

    @pytest.mark.asyncio
    async def test_router_skips_unmatched_targets(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(
                    id="r", type="router", depends=["a"],
                    routes=[
                        Route(match=None, to=["b"]),
                    ],
                ),
                Node(id="b", task="B", depends=["r"]),
                Node(id="c", task="C", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"something"),  # a
                _make_subprocess_mock(b"b done"),     # b
            ]
            results = await runner.run()

        completed_ids = {r.node_id for r in results}
        assert "b" in completed_ids
        assert "c" not in completed_ids

    @pytest.mark.asyncio
    async def test_router_max_limit_with_back_edge(self):
        """Route with max=1 fires once via back-edge, then falls through on re-evaluation."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do work"),
                Node(
                    id="r", type="router", depends=["do"],
                    routes=[
                        Route(match="critical", to=["do"], max=1),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"critical error"),  # do (1st run)
                _make_subprocess_mock(b"fixed"),            # do (back-edge re-run)
                _make_subprocess_mock(b"done output"),      # done (fallback on re-eval)
            ]
            results = await runner.run()

        node_ids = [r.node_id for r in results]
        do_count = node_ids.count("do")
        assert do_count == 2  # initial + 1 back-edge re-run
        assert "done" in node_ids
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_router_no_routes_matched(self):
        """When no route matches and there's no fallback, targets are skipped."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(
                    id="r", type="router", depends=["a"],
                    routes=[
                        Route(match="specific_pattern", to=["b"]),
                    ],
                ),
                Node(id="b", task="B", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"no match here")
            results = await runner.run()

        completed_ids = {r.node_id for r in results}
        assert "a" in completed_ids
        assert "b" not in completed_ids


# ===========================================================================
# 10. Runner — Back-edge / loop
# ===========================================================================


class TestRunnerBackEdge:
    @pytest.mark.asyncio
    async def test_back_edge_retries(self):
        """Router back-edge causes target to re-execute, then fallback."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do work"),
                Node(
                    id="check", type="router", depends=["do"],
                    routes=[
                        Route(match="RETRY", to=["do"], max=2),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["check"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"RETRY"),     # do (1st)
                _make_subprocess_mock(b"RETRY"),     # do (2nd, back-edge)
                _make_subprocess_mock(b"OK"),        # do (3rd, back-edge)
                _make_subprocess_mock(b"finished"),  # done
            ]
            results = await runner.run()

        do_results = [r for r in results if r.node_id == "do"]
        assert len(do_results) == 3  # initial + 2 retries
        assert "done" in {r.node_id for r in results}
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        """max_iterations limit stops execution."""
        wf = Workflow(
            name="t",
            max_iterations=2,
            nodes=[
                Node(id="do", task="Do"),
                Node(
                    id="check", type="router", depends=["do"],
                    routes=[
                        Route(match="RETRY", to=["do"], max=10),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["check"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"RETRY")
            results = await runner.run()

        assert wf.status == "loop_limit"

    @pytest.mark.asyncio
    async def test_back_edge_resets_downstream(self):
        """When a back-edge fires, downstream nodes are reset and re-run."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do"),
                Node(id="process", task="Process: {nodes.do.output}", depends=["do"]),
                Node(
                    id="check", type="router", depends=["process"],
                    routes=[
                        Route(match="FAIL", to=["do"], max=2),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["check"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"attempt1"),  # do
                _make_subprocess_mock(b"FAIL"),       # process
                _make_subprocess_mock(b"attempt2"),  # do (back-edge)
                _make_subprocess_mock(b"OK"),         # process (re-run)
                _make_subprocess_mock(b"done"),       # done
            ]
            results = await runner.run()

        do_results = [r for r in results if r.node_id == "do"]
        process_results = [r for r in results if r.node_id == "process"]
        assert len(do_results) == 2
        assert len(process_results) == 2
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_multi_router_shared_downstream(self):
        """Node depending on two routers runs when one skips and the other activates."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="start", task="Start"),
                Node(
                    id="r1", type="router", depends=["start"],
                    routes=[
                        Route(match="GO", to=["mid"]),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="mid", task="Mid", depends=["r1"]),
                Node(
                    id="r2", type="router", depends=["mid"],
                    routes=[
                        Route(match="RETRY", to=["mid"], max=1),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["r1", "r2"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"GO"),      # start
                _make_subprocess_mock(b"RETRY"),   # mid (1st)
                _make_subprocess_mock(b"OK"),      # mid (2nd, back-edge)
                _make_subprocess_mock(b"done"),    # done
            ]
            results = await runner.run()

        assert any(r.node_id == "done" for r in results), "done node should have executed"
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_loop_iter_counter_uses_retry_count(self):
        """Loop iter callback shows retry count (1, 2) not total activations (2, 4)."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do"),
                Node(
                    id="check", type="router", depends=["do"],
                    routes=[
                        Route(match="RETRY", to=["do"], max=2),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["check"]),
            ],
        )
        loop_calls: list[tuple[str, int, int]] = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                loop_calls.append((e.node_id, e.iteration, e.max_iter))
                if isinstance(e, LoopIterEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"RETRY"),
                _make_subprocess_mock(b"RETRY"),
                _make_subprocess_mock(b"OK"),
                _make_subprocess_mock(b"done"),
            ]
            await runner.run()

        assert len(loop_calls) == 2
        assert loop_calls[0][1] == 1  # first retry
        assert loop_calls[1][1] == 2  # second retry

    @pytest.mark.asyncio
    async def test_wave_reannouncement_after_back_edge(self):
        """After back-edge, later waves are re-announced only after loop completes."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do"),
                Node(id="proc", task="Proc", depends=["do"]),
                Node(
                    id="check", type="router", depends=["proc"],
                    routes=[
                        Route(match="RETRY", to=["do"], max=1),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["check"]),
            ],
        )
        wave_calls: list[tuple[int, int, list[str]]] = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                wave_calls.append((e.wave_idx, e.total_waves, e.node_ids))
                if isinstance(e, WaveReadyEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"attempt1"),
                _make_subprocess_mock(b"RETRY"),
                _make_subprocess_mock(b"attempt2"),
                _make_subprocess_mock(b"OK"),
                _make_subprocess_mock(b"done"),
            ]
            await runner.run()

        # "done" wave should be announced exactly once, after the loop completes
        done_wave_calls = [c for c in wave_calls if "done" in c[2]]
        assert len(done_wave_calls) == 1


# ===========================================================================
# 11. Runner — Auto-inference integration (depends + router gating)
# ===========================================================================


class TestRunnerAutoInference:
    """Auto-inferred depends interact correctly with router gating and back-edges."""

    @pytest.mark.asyncio
    async def test_router_target_not_auto_enqueued(self):
        """Router-gated node with auto-inferred depends waits for router."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="start", task="Start"),
                Node(id="validate", task="Val: {nodes.start.output}"),
                Node(
                    id="router", type="router", depends=["validate"],
                    routes=[
                        Route(match="FAIL", to=["start"], max=1),
                        Route(match=None, to=["process"]),
                    ],
                ),
                # auto-infers depends=[start] from {nodes.start.output}
                # should NOT run until router fires
                Node(id="process", task="Proc: {nodes.start.output}"),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as me:
            me.side_effect = [
                _make_subprocess_mock(b"data"),
                _make_subprocess_mock(b"PASS"),
                _make_subprocess_mock(b"done"),
            ]
            results = await runner.run()

        proc_results = [r for r in results if r.node_id == "process"]
        assert len(proc_results) == 1, "process should run exactly once"

    @pytest.mark.asyncio
    async def test_loop_retry_auto_inferred_deps(self):
        """Full loop-retry scenario with auto-inference: no false loop on process."""
        wf = Workflow(
            name="lr",
            max_iterations=20,
            nodes=[
                Node(id="g", task="Gen"),
                Node(id="v", task="Val: {nodes.g.output}"),
                Node(id="r", type="router", depends=["v"],
                     routes=[
                         Route(match="FAIL", to=["g"], max=3),
                         Route(match=None, to=["p"]),
                     ]),
                Node(id="p", task="Proc: {nodes.g.output}"),
                Node(id="o", task="Out: {nodes.p.output}"),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as me:
            me.side_effect = [
                _make_subprocess_mock(b"ok"), _make_subprocess_mock(b"FAIL err"),
                _make_subprocess_mock(b"ok"), _make_subprocess_mock(b"FAIL err"),
                _make_subprocess_mock(b"ok"), _make_subprocess_mock(b"FAIL err"),
                _make_subprocess_mock(b"ok"), _make_subprocess_mock(b"PASS"),
                _make_subprocess_mock(b"done"), _make_subprocess_mock(b"done"),
            ]
            results = await runner.run()

        proc_count = sum(1 for r in results if r.node_id == "p")
        out_count = sum(1 for r in results if r.node_id == "o")
        assert proc_count == 1, f"process ran {proc_count}x (expect 1)"
        assert out_count == 1, f"output ran {out_count}x (expect 1)"
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_explicit_depends_router_gate_only(self):
        """Node with explicit depends on router (no template ref) still works."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(id="r", type="router", depends=["a"],
                     routes=[Route(match=None, to=["b"])]),
                # b depends on r but references a's output (auto-inferred)
                Node(id="b", task="B: {nodes.a.output}", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as me:
            me.side_effect = [
                _make_subprocess_mock(b"data"),
                _make_subprocess_mock(b"b done"),
            ]
            results = await runner.run()

        b_results = [r for r in results if r.node_id == "b"]
        assert len(b_results) == 1
        assert wf.status == "done"


# ===========================================================================
# 12. Runner — Error handling
# ===========================================================================


class TestRunnerErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Slow task")])
        runner = DAGRunner(wf, timeout=1)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            proc = MagicMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_exec.return_value = proc
            results = await runner.run()

        assert results[0].exit_code == 1
        assert "timed out" in results[0].error
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_subprocess_exception(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Fail")])
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = RuntimeError("spawn failed")
            results = await runner.run()

        assert results[0].exit_code == 1
        assert "spawn failed" in results[0].error

    @pytest.mark.asyncio
    async def test_parallel_branch_failure_independent(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="root", task="Root"),
                Node(id="a", task="A ok", depends=["root"]),
                Node(id="b", task="B fail", depends=["root"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"root"),
                _make_subprocess_mock(b"a ok"),
                _make_subprocess_mock(b"b error", returncode=1),
            ]
            results = await runner.run()

        a_result = next(r for r in results if r.node_id == "a")
        b_result = next(r for r in results if r.node_id == "b")
        assert a_result.exit_code == 0
        assert b_result.exit_code == 1

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_stored(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Fail")])
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"error output", returncode=2)
            results = await runner.run()

        assert results[0].exit_code == 2
        assert results[0].output == "error output"


# ===========================================================================
# 12. Runner — Callbacks
# ===========================================================================


class TestRunnerCallbacks:
    @pytest.mark.asyncio
    async def test_on_node_done_callback(self):
        wf = _simple_linear_wf(2)
        calls = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                calls.append((e.node_id, e.result.node_id, e.wave_idx))
                if isinstance(e, NodeDoneEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a"),
                _make_subprocess_mock(b"b"),
            ]
            await runner.run()

        assert len(calls) == 2
        assert calls[0][0] == "n0"
        assert calls[1][0] == "n1"

    @pytest.mark.asyncio
    async def test_on_node_skip_callback(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(
                    id="r", type="router", depends=["a"],
                    routes=[Route(match=None, to=["b"])],
                ),
                Node(id="b", task="B", depends=["r"]),
                Node(id="c", task="C", depends=["r"]),
            ],
        )
        skip_calls = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                skip_calls.append((e.node_id, e.reason))
                if isinstance(e, NodeSkippedEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a"),
                _make_subprocess_mock(b"b"),
            ]
            await runner.run()

        skipped_ids = [nid for nid, _ in skip_calls]
        assert "c" in skipped_ids

    @pytest.mark.asyncio
    async def test_on_wave_start_callback(self):
        wf = _simple_linear_wf(2)
        wave_calls = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                wave_calls.append((e.wave_idx, e.total_waves, e.node_ids))
                if isinstance(e, WaveReadyEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a"),
                _make_subprocess_mock(b"b"),
            ]
            await runner.run()

        assert len(wave_calls) >= 1

    @pytest.mark.asyncio
    async def test_on_condition_callback(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="a", task="A"),
                Node(
                    id="r", type="router", depends=["a"],
                    routes=[
                        Route(match="yes", to=["b"]),
                        Route(match=None, to=["b"]),
                    ],
                ),
                Node(id="b", task="B", depends=["r"]),
            ],
        )
        cond_calls = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                cond_calls.append((e.router_id, e.matched, e.branch, e.targets))
                if isinstance(e, RouterConditionEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"yes please"),
                _make_subprocess_mock(b"b"),
            ]
            await runner.run()

        assert len(cond_calls) >= 1
        assert cond_calls[0][0] == "r"
        assert cond_calls[0][1] is True
        assert cond_calls[0][3] == ["b"]

    @pytest.mark.asyncio
    async def test_on_loop_iter_callback(self):
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do"),
                Node(
                    id="check", type="router", depends=["do"],
                    routes=[
                        Route(match="RETRY", to=["do"], max=2),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["check"]),
            ],
        )
        loop_calls = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                loop_calls.append((e.node_id, e.iteration, e.max_iter))
                if isinstance(e, LoopIterEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"RETRY"),  # do (1st)
                _make_subprocess_mock(b"OK"),     # do (2nd, back-edge)
                _make_subprocess_mock(b"done"),   # done
            ]
            await runner.run()

        assert len(loop_calls) >= 1
        assert loop_calls[0][0] == "do"

    @pytest.mark.asyncio
    async def test_on_progress_callback(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="A")])
        progress_msgs = []
        runner = DAGRunner(
            wf,
            on_event=lambda e: (
                progress_msgs.append(e.message)
                if isinstance(e, ProgressEvent) else None
            ),
        )
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"ok")
            await runner.run()

        assert len(progress_msgs) >= 1

    @pytest.mark.asyncio
    async def test_callbacks_none_are_safe(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="A")])
        runner = DAGRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"ok")
            results = await runner.run()

        assert len(results) == 1
        assert wf.status == "done"


# ===========================================================================
# 13. CLI command tests
# ===========================================================================


class TestWorkflowCommand:
    @pytest.fixture
    def _setup_registry(self, tmp_path: Path):
        _write_yaml(tmp_path / "demo.yaml", {
            "name": "demo",
            "description": "Demo workflow",
            "nodes": [{"id": "a", "task": "Hello"}],
        })
        return tmp_path

    @pytest.mark.asyncio
    async def test_list_subcommand(self, _setup_registry, tmp_path: Path):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        reg = WorkflowRegistry([tmp_path])
        app = MagicMock()
        app.workflow_registry = reg
        display = MagicMock()
        display.workflow_list.return_value = "  demo                 Demo workflow"

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="list"))
        assert result.kind == "continue"
        display.workflow_list.assert_called_once()
        display.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_show_subcommand(self, _setup_registry, tmp_path: Path):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        reg = WorkflowRegistry([tmp_path])
        app = MagicMock()
        app.workflow_registry = reg
        display = MagicMock()
        display.workflow_show.return_value = "demo\n  1 nodes"

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="show demo"))
        assert result.kind == "continue"
        display.workflow_show.assert_called_once()
        display.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_show_not_found(self, _setup_registry, tmp_path: Path):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        reg = WorkflowRegistry([tmp_path])
        app = MagicMock()
        app.workflow_registry = reg
        display = MagicMock()

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="show nope"))
        assert result.kind == "continue"
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_returns_prompt(self):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(args="create a research workflow"))
        assert result.kind == "prompt"
        assert "workflow" in result.prompt.lower()
        assert "nodes" in result.prompt

    @pytest.mark.asyncio
    async def test_run_missing_args(self):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        display = MagicMock()
        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(display=display, args="run"))
        assert result.kind == "continue"
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_not_found(self, _setup_registry, tmp_path: Path):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        reg = WorkflowRegistry([tmp_path])
        app = MagicMock()
        app.workflow_registry = reg
        display = MagicMock()

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="run nope"))
        assert result.kind == "continue"
        display.warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_parses_key_value_args(self, _setup_registry, tmp_path: Path):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        reg = WorkflowRegistry([tmp_path])
        app = MagicMock()
        app.workflow_registry = reg
        display = MagicMock()

        cmd = WorkflowCommand()
        with patch("mocode.app.cli.commands.workflow.DAGRunner") as MockRunner:
            mock_runner = AsyncMock()
            mock_runner.run = AsyncMock(return_value=[])
            MockRunner.return_value = mock_runner

            result = await cmd.run(_make_ctx(
                app=app, display=display,
                args="run demo path=/src verbose=true",
            ))

            mock_runner.run.assert_called_once_with(args={"path": "/src", "verbose": "true"})

    @pytest.mark.asyncio
    async def test_list_empty(self):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        app = MagicMock()
        app.workflow_registry = WorkflowRegistry([])
        display = MagicMock()

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="list"))
        assert result.kind == "continue"
        display.info.assert_called_once()


# ===========================================================================
# 14. Node context header
# ===========================================================================


class TestNodeContextHeader:
    def test_header_basic(self):
        """Header includes node ID and workflow name."""
        wf = Workflow(
            name="test-wf",
            nodes=[Node(id="scan", task="Scan", description="Scan code")],
        )
        runner = DAGRunner(wf)
        header = runner._build_node_context_header(wf.nodes[0], wf)
        assert 'node "scan"' in header
        assert 'workflow "test-wf"' in header

    def test_header_includes_description(self):
        """Header includes node's own description."""
        wf = Workflow(
            name="wf",
            nodes=[Node(id="a", task="A", description="Analyze stuff")],
        )
        runner = DAGRunner(wf)
        header = runner._build_node_context_header(wf.nodes[0], wf)
        assert "Description: Analyze stuff" in header

    def test_header_no_description(self):
        """Header works when node has no description."""
        wf = Workflow(name="wf", nodes=[Node(id="a", task="A")])
        runner = DAGRunner(wf)
        header = runner._build_node_context_header(wf.nodes[0], wf)
        assert "Description:" not in header
        assert 'node "a"' in header

    def test_header_input_from(self):
        """Header lists input nodes with descriptions."""
        wf = Workflow(
            name="wf",
            nodes=[
                Node(id="overview", task="Overview", description="Analyze structure"),
                Node(id="scan", task="Scan", description="Scan code", depends=["overview"]),
            ],
        )
        runner = DAGRunner(wf)
        header = runner._build_node_context_header(wf.node_map["scan"], wf)
        assert "Input from:" in header
        assert "- overview: Analyze structure" in header

    def test_header_output_to(self):
        """Header lists output nodes with descriptions."""
        wf = Workflow(
            name="wf",
            nodes=[
                Node(id="scan", task="Scan", description="Scan code"),
                Node(id="report", task="Report", description="Generate report", depends=["scan"]),
            ],
        )
        runner = DAGRunner(wf)
        header = runner._build_node_context_header(wf.node_map["scan"], wf)
        assert "Output to:" in header
        assert "- report: Generate report" in header

    def test_header_full_diamond(self):
        """Header for middle node in diamond shows both input and output."""
        wf = Workflow(
            name="diamond",
            nodes=[
                Node(id="root", task="Root", description="Root step"),
                Node(id="a", task="A", description="Branch A", depends=["root"]),
                Node(id="b", task="B", description="Branch B", depends=["root"]),
                Node(id="merge", task="Merge", description="Merge results", depends=["a", "b"]),
            ],
        )
        runner = DAGRunner(wf)

        # Root node: only output
        root_header = runner._build_node_context_header(wf.node_map["root"], wf)
        assert "Input from:" not in root_header
        assert "Output to:" in root_header
        assert "- a: Branch A" in root_header
        assert "- b: Branch B" in root_header

        # Merge node: only input
        merge_header = runner._build_node_context_header(wf.node_map["merge"], wf)
        assert "Input from:" in merge_header
        assert "- a: Branch A" in merge_header
        assert "- b: Branch B" in merge_header
        assert "Output to:" not in merge_header

    def test_header_no_deps_no_dependents(self):
        """Header for isolated node has no Input/Output sections."""
        wf = Workflow(name="wf", nodes=[Node(id="solo", task="Solo")])
        runner = DAGRunner(wf)
        header = runner._build_node_context_header(wf.nodes[0], wf)
        assert "Input from:" not in header
        assert "Output to:" not in header

    @pytest.mark.asyncio
    async def test_runner_injects_header_when_enabled(self):
        """When node_context=True, subprocess receives header + task."""
        wf = Workflow(
            name="test-wf",
            nodes=[
                Node(id="a", task="Do thing", description="Do a thing"),
                Node(id="b", task="Next", description="Next step", depends=["a"]),
            ],
        )
        runner = DAGRunner(wf, node_context=True)
        prompts_received = []

        async def capture_exec(node_id, task, context_header=None):
            prompts_received.append((node_id, task, context_header))
            return NodeResult(
                node_id=node_id, task=task, output="ok",
                exit_code=0, duration=0.1,
            )

        with patch.object(runner, "_exec_node", side_effect=capture_exec):
            await runner.run()

        # Node "b" should have context header
        b_call = next(c for c in prompts_received if c[0] == "b")
        assert b_call[2] is not None  # context_header not None
        assert 'node "b"' in b_call[2]
        assert "Input from:" in b_call[2]
        assert "- a: Do a thing" in b_call[2]

    @pytest.mark.asyncio
    async def test_runner_no_header_when_disabled(self):
        """When node_context=False, subprocess receives no header."""
        wf = Workflow(
            name="wf",
            nodes=[Node(id="a", task="Task", description="Desc")],
        )
        runner = DAGRunner(wf, node_context=False)
        headers_received = []

        async def capture_exec(node_id, task, context_header=None):
            headers_received.append(context_header)
            return NodeResult(
                node_id=node_id, task=task, output="ok",
                exit_code=0, duration=0.1,
            )

        with patch.object(runner, "_exec_node", side_effect=capture_exec):
            await runner.run()

        assert headers_received[0] is None


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
    compute_waves,
    fill_template,
    summarize,
    detailed_summarize,
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
        Node(id=f"n{i}", task=f"Task {i}", depends=[f"n{i - 1}"] if i > 0 else [])
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
        n = Node.from_dict(
            {
                "id": "decide",
                "type": "router",
                "depends": ["summary"],
                "routes": [
                    {"match": "critical", "to": ["fix"]},
                    {"match": None, "to": ["done"]},
                ],
            }
        )
        assert n.id == "decide"
        assert n.type == "router"
        assert n.task == ""
        assert n.depends == ["summary"]
        assert len(n.routes) == 2
        assert n.routes[0].match == "critical"
        assert n.routes[1].match is None


class TestNodeResultModel:
    def test_basic(self):
        nr = NodeResult(
            node_id="scan",
            task="Scan",
            output="found 3 issues",
            exit_code=0,
            duration=5.2,
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
            node_id="x",
            task="",
            output="",
            exit_code=1,
            duration=0.5,
            error="timed out",
        )
        assert nr.error == "timed out"
        assert nr.exit_code == 1


class TestDependsInference:
    """Tests for auto-inference of depends from task template references."""

    def test_infer_from_nodes_output(self):
        assert infer_depends_from_task("Check {nodes.scan.output}") == ["scan"]

    def test_node_from_dict_auto_infers(self):
        n = Node.from_dict({"id": "t", "task": "Process {nodes.src.output}"})
        assert "src" in n.depends


class TestWorkflowModel:
    def test_basic_construction(self):
        wf = Workflow(name="test", description="desc")
        assert wf.name == "test"
        assert wf.description == "desc"
        assert wf.nodes == []
        assert wf.max_iterations == 100

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

    def test_summary(self):
        wf = Workflow(name="test")
        results = [
            NodeResult(
                node_id="a", task="Do thing", output="ok", exit_code=0, duration=1.5
            )
        ]
        s = summarize(wf, results)
        assert "test" in s
        assert "[OK]" in s
        assert "Do thing" in s


# ===========================================================================
# 2. Validation tests
# ===========================================================================


class TestWorkflowValidation:
    def test_duplicate_id_via_yaml(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "dup.yaml",
            {
                "name": "dup",
                "nodes": [
                    {"id": "a", "task": "A"},
                    {"id": "a", "task": "A2"},
                ],
            },
        )
        with pytest.raises(ValueError, match="Duplicate"):
            Workflow.from_yaml(path)

    def test_cycle_without_router_back_edge_rejected(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "cycle.yaml",
            {
                "name": "test",
                "nodes": [
                    {"id": "a", "task": "A", "depends": ["b"]},
                    {"id": "b", "task": "B", "depends": ["a"]},
                ],
            },
        )
        with pytest.raises(ValueError, match="Cycle"):
            Workflow.from_yaml(path)


# ===========================================================================
# 3. from_yaml parsing tests
# ===========================================================================


class TestWorkflowFromYaml:
    def test_basic_parse(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "basic.yaml",
            {
                "name": "basic",
                "description": "A test",
                "nodes": [
                    {"id": "a", "task": "Hello"},
                    {"id": "b", "task": "World", "depends": ["a"]},
                ],
            },
        )
        wf = Workflow.from_yaml(path)
        assert wf.name == "basic"
        assert wf.description == "A test"
        assert len(wf.nodes) == 2
        assert wf.nodes[1].depends == ["a"]


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

    def test_nodes_output(self):
        ctx = {"nodes": {"scan": {"output": "found 5 files"}}}
        assert fill_template("{nodes.scan.output}", ctx) == "found 5 files"

    def test_unknown_placeholder_kept(self):
        assert fill_template("{unknown.thing}", {}) == "{unknown.thing}"


# ===========================================================================
# 5. Wave computation tests
# ===========================================================================


class TestComputeWaves:
    def test_linear_chain(self):
        wf = _simple_linear_wf(3)
        waves = compute_waves(wf)
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["n0"]
        assert [n.id for n in waves[1]] == ["n1"]
        assert [n.id for n in waves[2]] == ["n2"]

    def test_diamond(self):
        wf = _simple_parallel_wf()
        waves = compute_waves(wf)
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["root"]
        assert set(n.id for n in waves[1]) == {"a", "b"}
        assert [n.id for n in waves[2]] == ["merge"]


# ===========================================================================
# 6. Runner — Linear chain
# ===========================================================================


class TestRunnerLinearChain:
    @pytest.mark.asyncio
    async def test_linear_chain_abc(self):
        wf = _simple_linear_wf(3)
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
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
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"hello"),
                _make_subprocess_mock(b"world"),
            ]
            results = await runner.run()

        assert results[1].task == "Result: hello"


# ===========================================================================
# 7. Runner — Router
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
                    id="r",
                    type="router",
                    depends=["a"],
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
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"yes please"),  # a
                _make_subprocess_mock(b"b done"),  # b (activated by route 0)
            ]
            results = await runner.run()

        completed_ids = [r.node_id for r in results]
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
                    id="r",
                    type="router",
                    depends=["do"],
                    routes=[
                        Route(match="critical", to=["do"], max=1),
                        Route(match=None, to=["done"]),
                    ],
                ),
                Node(id="done", task="Done", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"critical error"),  # do (1st run)
                _make_subprocess_mock(b"fixed"),  # do (back-edge re-run)
                _make_subprocess_mock(b"done output"),  # done (fallback on re-eval)
            ]
            results = await runner.run()

        node_ids = [r.node_id for r in results]
        do_count = node_ids.count("do")
        assert do_count == 2  # initial + 1 back-edge re-run
        assert "done" in node_ids


# ===========================================================================
# 8. Runner — Error handling
# ===========================================================================


class TestRunnerErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Slow task")])
        runner = DAGRunner(wf, timeout=1)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            proc = MagicMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_exec.return_value = proc
            results = await runner.run()

        assert results[0].exit_code == 1
        assert "timed out" in results[0].error

    @pytest.mark.asyncio
    async def test_subprocess_exception(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Fail")])
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = RuntimeError("spawn failed")
            results = await runner.run()

        assert results[0].exit_code == 1
        assert "spawn failed" in results[0].error

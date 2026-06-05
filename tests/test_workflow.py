"""Tests for workflow engine — models, fill_template, registry, waves, runner, command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.app.workflow import (
    LoopIterEvent,
    MapFanOutEvent,
    MapItemDoneEvent,
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
    parse_items,
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


# ===========================================================================
# 9. Workflow command — menu pending input
# ===========================================================================


class TestWorkflowMenuPendingInput:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action,expect_pending_input,expect_show",
        [("run", True, False), ("show", False, True), ("back", False, False)],
    )
    async def test_menu_action(self, action, expect_pending_input, expect_show):
        """Menu actions: run sets pending input, show displays details, back returns."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import WorkflowCommand

        wf = Workflow(
            name="test-wf",
            nodes=[Node(id="a", task="Do stuff")],
        )
        registry = MagicMock()
        registry.list.return_value = [wf]
        registry.get.return_value = wf

        app = MagicMock()
        app.workflow_registry = registry
        display = MagicMock()
        display.workflow_show.return_value = "tree view"
        ctx = _make_ctx(app=app, display=display)

        cmd = WorkflowCommand()

        with patch(
            "mocode.app.cli.commands.workflow.select",
            new_callable=AsyncMock,
        ) as mock_select:
            mock_select.side_effect = ["test-wf", action]
            result = await cmd._menu(ctx)

        assert result == CommandResult.CONTINUE
        if expect_pending_input:
            display.set_pending_input.assert_called_once_with("/workflow run test-wf")
        else:
            display.set_pending_input.assert_not_called()
        if expect_show:
            display.workflow_show.assert_called_once_with(wf)
        else:
            display.workflow_show.assert_not_called()


# ===========================================================================
# 10. parse_items tests
# ===========================================================================


class TestParseItems:
    def test_lines_mode(self):
        assert parse_items("alpha\nbeta\ngamma") == ["alpha", "beta", "gamma"]

    def test_lines_skips_empty(self):
        assert parse_items("a\n\nb\n\nc") == ["a", "b", "c"]

    def test_lines_strips(self):
        assert parse_items("  a  \n  b  ") == ["a", "b"]

    def test_csv_mode(self):
        assert parse_items("a,b,c", "csv") == ["a", "b", "c"]

    def test_csv_strips(self):
        assert parse_items(" a , b , c ", "csv") == ["a", "b", "c"]

    def test_json_mode(self):
        assert parse_items('["a", "b", "c"]', "json") == ["a", "b", "c"]

    def test_json_numbers(self):
        assert parse_items("[1, 2, 3]", "json") == ["1", "2", "3"]

    def test_json_invalid_falls_back_to_lines(self):
        assert parse_items("not json", "json") == ["not json"]

    def test_empty_input(self):
        assert parse_items("") == []


# ===========================================================================
# 11. Map node model tests
# ===========================================================================


class TestMapNodeModel:
    def test_from_dict_map(self):
        n = Node.from_dict(
            {
                "id": "search",
                "type": "map",
                "items": "{nodes.gen.output}",
                "parse": "lines",
                "item_key": "keyword",
                "task": "Search {{keyword}}",
            }
        )
        assert n.type == "map"
        assert n.items == "{nodes.gen.output}"
        assert n.parse == "lines"
        assert n.item_key == "keyword"
        assert n.task == "Search {{keyword}}"

    def test_map_auto_infers_depends_from_items(self):
        n = Node.from_dict(
            {
                "id": "m",
                "type": "map",
                "items": "{nodes.gen.output}",
                "task": "Process {{item}}",
            }
        )
        assert "gen" in n.depends

    def test_map_auto_infers_depends_from_both_templates(self):
        n = Node.from_dict(
            {
                "id": "m",
                "type": "map",
                "items": "{nodes.a.output}",
                "task": "Use {nodes.b.output} and {{item}}",
            }
        )
        assert "a" in n.depends
        assert "b" in n.depends

    def test_map_default_fields(self):
        n = Node(id="m", type="map", items="{args.x}", task="Do {{item}}")
        assert n.parse == "lines"
        assert n.item_key == "item"


# ===========================================================================
# 12. Map node validation tests
# ===========================================================================


class TestMapNodeValidation:
    def test_map_missing_items_rejected(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "no_items.yaml",
            {
                "name": "t",
                "nodes": [
                    {"id": "m", "type": "map", "task": "Do {{item}}"},
                ],
            },
        )
        with pytest.raises(ValueError, match="must have 'items'"):
            Workflow.from_yaml(path)

    def test_map_missing_task_rejected(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "no_task.yaml",
            {
                "name": "t",
                "nodes": [
                    {"id": "m", "type": "map", "items": "{args.x}"},
                ],
            },
        )
        with pytest.raises(ValueError, match="must have 'task'"):
            Workflow.from_yaml(path)

    def test_map_with_routes_rejected(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "routes.yaml",
            {
                "name": "t",
                "nodes": [
                    {
                        "id": "m",
                        "type": "map",
                        "items": "{args.x}",
                        "task": "Do {{item}}",
                        "routes": [{"match": "x", "to": ["y"]}],
                    },
                ],
            },
        )
        with pytest.raises(ValueError, match="must not have 'routes'"):
            Workflow.from_yaml(path)

    def test_map_invalid_parse_rejected(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "bad_parse.yaml",
            {
                "name": "t",
                "nodes": [
                    {
                        "id": "m",
                        "type": "map",
                        "items": "{args.x}",
                        "parse": "xml",
                        "task": "Do {{item}}",
                    },
                ],
            },
        )
        with pytest.raises(ValueError, match="invalid parse"):
            Workflow.from_yaml(path)


# ===========================================================================
# 13. Workflow concurrency field tests
# ===========================================================================


class TestWorkflowConcurrency:
    def test_default_concurrency(self):
        wf = Workflow(name="t", nodes=[])
        assert wf.concurrency == 1

    def test_concurrency_from_yaml(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "conc.yaml",
            {
                "name": "t",
                "concurrency": 3,
                "nodes": [{"id": "a", "task": "A"}],
            },
        )
        wf = Workflow.from_yaml(path)
        assert wf.concurrency == 3


# ===========================================================================
# 14. Runner — Map node
# ===========================================================================


class TestRunnerMapNode:
    @pytest.mark.asyncio
    async def test_map_fan_out_three_items(self):
        """Map node expands 3 items, runs each as a subprocess, concatenates output."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="gen", task="Generate keywords"),
                Node(
                    id="search",
                    type="map",
                    items="{nodes.gen.output}",
                    parse="lines",
                    item_key="kw",
                    task="Search for {{kw}}",
                    depends=["gen"],
                ),
                Node(
                    id="report",
                    task="Report: {nodes.search.output}",
                    depends=["search"],
                ),
            ],
        )
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            # gen outputs 3 keywords, each map child returns a result, report summarizes
            mock_exec.side_effect = [
                _make_subprocess_mock(b"alpha\nbeta\ngamma"),  # gen
                _make_subprocess_mock(b"result-alpha"),  # search::0
                _make_subprocess_mock(b"result-beta"),  # search::1
                _make_subprocess_mock(b"result-gamma"),  # search::2
                _make_subprocess_mock(b"final report"),  # report
            ]
            results = await runner.run()

        # gen + 3 map children + map node itself + report = 5 results
        node_ids = [r.node_id for r in results]
        assert "gen" in node_ids
        assert "search::0" in node_ids
        assert "search::1" in node_ids
        assert "search::2" in node_ids
        assert "search" in node_ids
        assert "report" in node_ids

        # map node output should be concatenation of children
        map_result = next(r for r in results if r.node_id == "search")
        assert "result-alpha" in map_result.output
        assert "result-beta" in map_result.output
        assert "result-gamma" in map_result.output
        assert "---" in map_result.output  # separator

        # report received the concatenated map output
        report_result = next(r for r in results if r.node_id == "report")
        assert "result-alpha" in report_result.task

    @pytest.mark.asyncio
    async def test_map_with_csv_parse(self):
        """Map node with csv parse splits comma-separated items."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="m",
                    type="map",
                    items="{nodes.gen.output}",
                    parse="csv",
                    item_key="x",
                    task="Process {{x}}",
                    depends=["gen"],
                ),
            ],
        )
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a,b,c"),
                _make_subprocess_mock(b"r1"),
                _make_subprocess_mock(b"r2"),
                _make_subprocess_mock(b"r3"),
            ]
            results = await runner.run()

        child_ids = [r.node_id for r in results if "::" in r.node_id]
        assert len(child_ids) == 3

    @pytest.mark.asyncio
    async def test_map_with_json_parse(self):
        """Map node with json parse handles JSON arrays."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="m",
                    type="map",
                    items="{nodes.gen.output}",
                    parse="json",
                    item_key="x",
                    task="Process {{x}}",
                    depends=["gen"],
                ),
            ],
        )
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b'["one", "two"]'),
                _make_subprocess_mock(b"r1"),
                _make_subprocess_mock(b"r2"),
            ]
            results = await runner.run()

        child_ids = [r.node_id for r in results if "::" in r.node_id]
        assert len(child_ids) == 2

    @pytest.mark.asyncio
    async def test_map_empty_items(self):
        """Map node with empty items produces empty output without spawning."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="m",
                    type="map",
                    items="{nodes.gen.output}",
                    task="Process {{item}}",
                    depends=["gen"],
                ),
            ],
        )
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b""),  # gen outputs empty
            ]
            results = await runner.run()

        # Only gen + map (empty) results, no children
        assert len(results) == 2
        map_result = next(r for r in results if r.node_id == "m")
        assert map_result.output == ""

    @pytest.mark.asyncio
    async def test_map_events_emitted(self):
        """Map node emits MapFanOutEvent and MapItemDoneEvent."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="m",
                    type="map",
                    items="{nodes.gen.output}",
                    task="Process {{item}}",
                    depends=["gen"],
                ),
            ],
        )
        events: list = []
        runner = DAGRunner(wf, on_event=events.append)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"x\ny"),
                _make_subprocess_mock(b"r1"),
                _make_subprocess_mock(b"r2"),
            ]
            await runner.run()

        fan_out = [e for e in events if isinstance(e, MapFanOutEvent)]
        item_done = [e for e in events if isinstance(e, MapItemDoneEvent)]
        assert len(fan_out) == 1
        assert fan_out[0].item_count == 2
        assert len(item_done) == 2

    @pytest.mark.asyncio
    async def test_map_with_concurrency(self):
        """Map node respects workflow concurrency via semaphore."""
        wf = Workflow(
            name="t",
            concurrency=2,
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="m",
                    type="map",
                    items="{nodes.gen.output}",
                    task="Process {{item}}",
                    depends=["gen"],
                ),
            ],
        )
        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a\nb\nc"),
                _make_subprocess_mock(b"r1"),
                _make_subprocess_mock(b"r2"),
                _make_subprocess_mock(b"r3"),
            ]
            results = await runner.run()

        child_ids = [r.node_id for r in results if "::" in r.node_id]
        assert len(child_ids) == 3

    @pytest.mark.asyncio
    async def test_map_yaml_round_trip(self, tmp_path: Path):
        """Full YAML → Workflow → runner round trip for a map workflow."""
        path = _write_yaml(
            tmp_path / "map_wf.yaml",
            {
                "name": "multi-search",
                "description": "Multi-keyword search",
                "concurrency": 2,
                "nodes": [
                    {"id": "gen", "task": "Generate 3 keywords for {args.topic}"},
                    {
                        "id": "search",
                        "type": "map",
                        "items": "{nodes.gen.output}",
                        "parse": "lines",
                        "item_key": "kw",
                        "task": "Search {{kw}}",
                        "depends": ["gen"],
                    },
                    {
                        "id": "report",
                        "task": "Summarize:\n{nodes.search.output}",
                        "depends": ["search"],
                    },
                ],
            },
        )
        wf = Workflow.from_yaml(path)
        assert wf.concurrency == 2
        assert wf.nodes[1].type == "map"
        assert wf.nodes[1].item_key == "kw"

        runner = DAGRunner(wf)
        with patch(
            "mocode.app.workflow.runner.asyncio.create_subprocess_exec"
        ) as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"ai\nml\ndl"),
                _make_subprocess_mock(b"ai-results"),
                _make_subprocess_mock(b"ml-results"),
                _make_subprocess_mock(b"dl-results"),
                _make_subprocess_mock(b"summary"),
            ]
            results = await runner.run(args={"topic": "technology"})

        node_ids = [r.node_id for r in results]
        assert "gen" in node_ids
        assert "search::0" in node_ids
        assert "search::1" in node_ids
        assert "search::2" in node_ids
        assert "search" in node_ids
        assert "report" in node_ids

        # Verify template filling for gen node
        gen_result = next(r for r in results if r.node_id == "gen")
        assert "technology" in gen_result.task

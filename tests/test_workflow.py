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
    ParamDef,
    Route,
    WaveReadyEvent,
    Workflow,
    WorkflowRegistry,
    compute_waves,
    fill_template,
    parse_args,
    parse_items,
)
from mocode.app.cli.workflow_renderer import summarize, detailed_summarize
from mocode.app.workflow.events import (
    NodeStartEvent,
    NodeToolBatchDoneEvent,
    NodeToolCallEvent,
)
from mocode.app.workflow.events import (
    ProgressEvent,
    RouterConditionEvent,
)
from mocode.app.workflow.models import infer_depends_from_task
from mocode.app.workflow.runner import DAGRunner
from mocode.app.workflow.state import RunState
from mocode.core.agent import AgentConfig, AgentLoop
from mocode.core.hook import HookRunner
from mocode.core.tool import ToolRegistry


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


class _MockProvider:
    """Minimal mock provider for DAGRunner tests."""
    model = "test-model"

    async def call(self, messages, system_prompt, tools, max_tokens):
        from mocode.core.provider import Response
        # Return the last user message as content
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m["content"] if isinstance(m["content"], str) else str(m["content"])
                break
        return Response(content=user_msg, tool_calls=None)


def _make_mock_agent() -> AgentLoop:
    """Create a minimal AgentLoop to serve as DAGRunner.parent_agent."""
    return AgentLoop(
        provider=_MockProvider(),
        system_prompt="test",
        tools=ToolRegistry(),
        hooks=HookRunner(),
        config=AgentConfig(),
    )


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
# 1. Models — Route, Node, NodeResult, depends inference
# ===========================================================================


class TestModels:
    def test_route_from_dict(self):
        r = Route.from_dict({"match": "error", "to": ["fix"], "max": 3})
        assert r.match == "error"
        assert r.to == ["fix"]
        assert r.max == 3

    def test_node_from_dict_router(self):
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

    def test_node_result_defaults(self):
        nr = NodeResult(
            node_id="scan", task="Scan", output="found 3 issues",
            exit_code=0, duration=5.2,
        )
        assert nr.node_id == "scan"
        assert nr.output == "found 3 issues"
        assert nr.exit_code == 0
        assert nr.error is None
        assert nr.status == "done"
        assert nr.iteration == 1

    def test_depends_inference(self):
        assert infer_depends_from_task("Check {nodes.scan.output}") == ["scan"]
        n = Node.from_dict({"id": "t", "task": "Process {nodes.src.output}"})
        assert "src" in n.depends


# ===========================================================================
# 2. Workflow model
# ===========================================================================


class TestWorkflowModel:
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
            nodes=[Node(id="a", task="A"), Node(id="b", task="B", depends=["a"])],
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
# 3. Parsing — YAML, validation, concurrency
# ===========================================================================


class TestWorkflowParsing:
    def test_basic_yaml(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "basic.yaml", {
            "name": "basic", "description": "A test",
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

    def test_duplicate_id_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "dup.yaml", {
            "name": "dup",
            "nodes": [{"id": "a", "task": "A"}, {"id": "a", "task": "A2"}],
        })
        with pytest.raises(ValueError, match="Duplicate"):
            Workflow.from_yaml(path)

    def test_cycle_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "cycle.yaml", {
            "name": "test",
            "nodes": [
                {"id": "a", "task": "A", "depends": ["b"]},
                {"id": "b", "task": "B", "depends": ["a"]},
            ],
        })
        with pytest.raises(ValueError, match="Cycle"):
            Workflow.from_yaml(path)

    def test_concurrency_from_yaml(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "conc.yaml", {
            "name": "t", "concurrency": 3,
            "nodes": [{"id": "a", "task": "A"}],
        })
        wf = Workflow.from_yaml(path)
        assert wf.concurrency == 3


# ===========================================================================
# 4. Template variables
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
# 5. Map node — model + validation
# ===========================================================================


class TestMapNode:
    def test_from_dict_map(self):
        n = Node.from_dict({
            "id": "search", "type": "map",
            "items": "{nodes.gen.output}", "parse": "lines",
            "item_key": "keyword", "task": "Search {keyword}",
        })
        assert n.type == "map"
        assert n.items == "{nodes.gen.output}"
        assert n.item_key == "keyword"
        assert n.task == "Search {keyword}"

    def test_auto_infers_depends(self):
        n = Node.from_dict({
            "id": "m", "type": "map",
            "items": "{nodes.a.output}",
            "task": "Use {nodes.b.output} and {item}",
        })
        assert "a" in n.depends
        assert "b" in n.depends

    def test_default_item_key(self):
        n = Node(id="m", type="map", items="{args.x}", task="Do {item}")
        assert n.item_key == "item"

    def test_missing_items_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "no_items.yaml", {
            "name": "t",
            "nodes": [{"id": "m", "type": "map", "task": "Do {item}"}],
        })
        with pytest.raises(ValueError, match="must have 'items'"):
            Workflow.from_yaml(path)

    def test_missing_task_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "no_task.yaml", {
            "name": "t",
            "nodes": [{"id": "m", "type": "map", "items": "{args.x}"}],
        })
        with pytest.raises(ValueError, match="must have 'task'"):
            Workflow.from_yaml(path)

    def test_with_routes_rejected(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "routes.yaml", {
            "name": "t",
            "nodes": [{
                "id": "m", "type": "map", "items": "{args.x}",
                "task": "Do {item}",
                "routes": [{"match": "x", "to": ["y"]}],
            }],
        })
        with pytest.raises(ValueError, match="must not have 'routes'"):
            Workflow.from_yaml(path)


# ===========================================================================
# 6. parse_items
# ===========================================================================


class TestParseItems:
    def test_lines_mode(self):
        assert parse_items("alpha\nbeta\ngamma") == ["alpha", "beta", "gamma"]

    def test_lines_skips_empty_and_strips(self):
        assert parse_items("  a  \n\n  b  \n") == ["a", "b"]

    def test_empty_input(self):
        assert parse_items("") == []


# ===========================================================================
# 7. Wave computation
# ===========================================================================


class TestComputeWaves:
    def test_linear_chain(self):
        waves = compute_waves(_simple_linear_wf(3))
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["n0"]
        assert [n.id for n in waves[1]] == ["n1"]
        assert [n.id for n in waves[2]] == ["n2"]

    def test_diamond(self):
        waves = compute_waves(_simple_parallel_wf())
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["root"]
        assert set(n.id for n in waves[1]) == {"a", "b"}
        assert [n.id for n in waves[2]] == ["merge"]


# ===========================================================================
# 8. Runner — Linear chain
# ===========================================================================


class TestRunnerLinearChain:
    @pytest.mark.asyncio
    async def test_linear_chain_abc(self):
        wf = _simple_linear_wf(3)
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())
        outputs = iter([
            NodeResult(node_id="n0", task="Task 0", output="out-a", exit_code=0, duration=0.1),
            NodeResult(node_id="n1", task="Task 1", output="out-b", exit_code=0, duration=0.1),
            NodeResult(node_id="n2", task="Task 2", output="out-c", exit_code=0, duration=0.1),
        ])
        with patch.object(runner, "_exec_node", side_effect=lambda nid, task, ch=None: next(outputs)):
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
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())

        async def _mock_exec(node_id, task, context_header=None):
            return NodeResult(node_id=node_id, task=task, output="hello" if node_id == "n0" else "world",
                              exit_code=0, duration=0.1)

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

        assert results[1].task == "Result: hello"


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
                Node(id="r", type="router", depends=["a"], routes=[
                    Route(match="yes", to=["b"]),
                    Route(match=None, to=["c"]),
                ]),
                Node(id="b", task="B", depends=["r"]),
                Node(id="c", task="C", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())
        outputs = {
            "a": NodeResult(node_id="a", task="A", output="yes please", exit_code=0, duration=0.1),
            "b": NodeResult(node_id="b", task="B", output="b done", exit_code=0, duration=0.1),
        }
        async def _mock_exec(node_id, task, context_header=None):
            return outputs[node_id]

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

        completed_ids = [r.node_id for r in results]
        assert "b" in completed_ids
        assert "c" not in completed_ids

    @pytest.mark.asyncio
    async def test_router_max_limit_with_back_edge(self):
        """Route with max=1 fires once via back-edge, then falls through."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="do", task="Do work"),
                Node(id="r", type="router", depends=["do"], routes=[
                    Route(match="critical", to=["do"], max=1),
                    Route(match=None, to=["done"]),
                ]),
                Node(id="done", task="Done", depends=["r"]),
            ],
        )
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())
        call_count = {"do": 0}
        async def _mock_exec(node_id, task, context_header=None):
            if node_id == "do":
                call_count["do"] += 1
                if call_count["do"] == 1:
                    return NodeResult(node_id="do", task=task, output="critical error", exit_code=0, duration=0.1)
                return NodeResult(node_id="do", task=task, output="fixed", exit_code=0, duration=0.1)
            return NodeResult(node_id=node_id, task=task, output="done output", exit_code=0, duration=0.1)

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

        node_ids = [r.node_id for r in results]
        assert node_ids.count("do") == 2
        assert "done" in node_ids


# ===========================================================================
# 10. Runner — Error handling
# ===========================================================================


class TestRunnerErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout(self):
        """When _exec_node returns a timeout error, the runner records it."""
        wf = Workflow(name="t", nodes=[Node(id="a", task="Slow task")])
        runner = DAGRunner(wf, parent_agent=_make_mock_agent(), timeout=1)

        # Simulate what _exec_node does on timeout: return error NodeResult
        async def _timeout_exec(node_id, task, context_header=None):
            return NodeResult(
                node_id=node_id, task=task, output="",
                exit_code=1, duration=1.0, error="timed out after 1s",
            )

        with patch.object(runner, "_exec_node", side_effect=_timeout_exec):
            results = await runner.run()

        assert results[0].exit_code == 1
        assert "timed out" in results[0].error

    @pytest.mark.asyncio
    async def test_subprocess_exception(self):
        wf = Workflow(name="t", nodes=[Node(id="a", task="Fail")])
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())

        # Simulate what _exec_node does on exception: return error NodeResult
        async def _fail_exec(node_id, task, context_header=None):
            return NodeResult(
                node_id=node_id, task=task, output="",
                exit_code=1, duration=0.1, error="spawn failed",
            )

        with patch.object(runner, "_exec_node", side_effect=_fail_exec):
            results = await runner.run()

        assert results[0].exit_code == 1
        assert "spawn failed" in results[0].error


# ===========================================================================
# 11. Runner — Map node
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
                    id="search", type="map",
                    items="{nodes.gen.output}", item_key="kw",
                    task="Search for {kw}", depends=["gen"],
                ),
                Node(id="report", task="Report: {nodes.search.output}", depends=["search"]),
            ],
        )
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())
        call_idx = {"i": 0}
        outputs = [
            NodeResult(node_id="gen", task="Generate keywords", output="alpha\nbeta\ngamma", exit_code=0, duration=0.1),
            NodeResult(node_id="search::0", task="Search for alpha", output="result-alpha", exit_code=0, duration=0.1),
            NodeResult(node_id="search::1", task="Search for beta", output="result-beta", exit_code=0, duration=0.1),
            NodeResult(node_id="search::2", task="Search for gamma", output="result-gamma", exit_code=0, duration=0.1),
            NodeResult(node_id="report", task="Report: ...", output="final report", exit_code=0, duration=0.1),
        ]
        async def _mock_exec(node_id, task, context_header=None):
            i = call_idx["i"]
            call_idx["i"] += 1
            return outputs[i]

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

        node_ids = [r.node_id for r in results]
        assert "gen" in node_ids
        assert "search::0" in node_ids
        assert "search::2" in node_ids
        assert "search" in node_ids
        assert "report" in node_ids

        # map node output = concatenation of children
        map_result = next(r for r in results if r.node_id == "search")
        assert "result-alpha" in map_result.output
        assert "---" in map_result.output

    @pytest.mark.asyncio
    async def test_map_empty_items(self):
        """Map node with empty items produces empty output without spawning."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="m", type="map",
                    items="{nodes.gen.output}", task="Process {item}",
                    depends=["gen"],
                ),
            ],
        )
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())

        async def _mock_exec(node_id, task, context_header=None):
            return NodeResult(node_id=node_id, task=task, output="", exit_code=0, duration=0.1)

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

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
                    id="m", type="map",
                    items="{nodes.gen.output}", task="Process {item}",
                    depends=["gen"],
                ),
            ],
        )
        events: list = []
        runner = DAGRunner(wf, parent_agent=_make_mock_agent(), on_event=events.append)

        async def _mock_exec(node_id, task, context_header=None):
            return NodeResult(node_id=node_id, task=task, output="r" if "::" in node_id else "x\ny",
                              exit_code=0, duration=0.1)

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
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
            name="t", concurrency=2,
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="m", type="map",
                    items="{nodes.gen.output}", task="Process {item}",
                    depends=["gen"],
                ),
            ],
        )
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())

        async def _mock_exec(node_id, task, context_header=None):
            return NodeResult(node_id=node_id, task=task, output="r" if "::" in node_id else "a\nb\nc",
                              exit_code=0, duration=0.1)

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

        child_ids = [r.node_id for r in results if "::" in r.node_id]
        assert len(child_ids) == 3


# ===========================================================================
# 11b. Runner — NodeStartEvent ordering with concurrency
# ===========================================================================


class TestRunnerStartEventOrdering:
    @pytest.mark.asyncio
    async def test_start_event_deferred_by_concurrency(self):
        """NodeStartEvent should only fire when the node actually starts executing,
        not when the wave is announced. With concurrency=1, the second node's
        start event must arrive AFTER the first node's done event."""
        import time as _time

        wf = Workflow(
            name="t",
            concurrency=1,
            nodes=[
                Node(id="root", task="Root"),
                Node(id="a", task="A", depends=["root"]),
                Node(id="b", task="B", depends=["root"]),
            ],
        )
        events: list = []
        runner = DAGRunner(wf, parent_agent=_make_mock_agent(), on_event=events.append)

        first_done_time: float | None = None
        second_start_time: float | None = None

        async def _mock_exec(node_id, task, context_header=None):
            nonlocal first_done_time, second_start_time
            if node_id == "a":
                await asyncio.sleep(0.05)  # simulate work
                first_done_time = _time.monotonic()
            elif node_id == "b":
                second_start_time = _time.monotonic()
            return NodeResult(
                node_id=node_id, task=task, output="ok",
                exit_code=0, duration=0.05,
            )

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            await runner.run()

        # All 3 nodes should have start events
        start_events = [e for e in events if isinstance(e, NodeStartEvent)]
        assert len(start_events) == 3

        # All 3 nodes should have done events
        done_events = [e for e in events if isinstance(e, NodeDoneEvent)]
        assert len(done_events) == 3

        # Key assertion: b's start must come after a's done
        assert first_done_time is not None
        assert second_start_time is not None
        assert second_start_time >= first_done_time, (
            f"Node 'b' started at {second_start_time} but node 'a' finished at {first_done_time}. "
            "NodeStartEvent should be deferred until the semaphore is acquired."
        )

    @pytest.mark.asyncio
    async def test_start_event_order_in_events_list(self):
        """With concurrency=1, event list order should be:
        [root_start, root_done, a_start, ..., a_done, b_start, ..., b_done]
        Not: [root_start, root_done, a_start, b_start, ...]"""
        wf = Workflow(
            name="t",
            concurrency=1,
            nodes=[
                Node(id="root", task="Root"),
                Node(id="a", task="A", depends=["root"]),
                Node(id="b", task="B", depends=["root"]),
            ],
        )
        events: list = []
        runner = DAGRunner(wf, parent_agent=_make_mock_agent(), on_event=events.append)

        async def _mock_exec(node_id, task, context_header=None):
            return NodeResult(
                node_id=node_id, task=task, output="ok",
                exit_code=0, duration=0.01,
            )

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            await runner.run()

        # Extract the sequence of start/done events
        relevant = [
            (type(e).__name__, e.node_id)
            for e in events
            if isinstance(e, (NodeStartEvent, NodeDoneEvent))
        ]
        # Expected: start(root), done(root), start(a), done(a), start(b), done(b)
        # With concurrency=1, 'a' must finish before 'b' starts
        a_done_idx = next(i for i, (t, nid) in enumerate(relevant) if t == "NodeDoneEvent" and nid == "a")
        b_start_idx = next(i for i, (t, nid) in enumerate(relevant) if t == "NodeStartEvent" and nid == "b")
        assert b_start_idx > a_done_idx, (
            f"Event order: {relevant}. 'b' start (idx {b_start_idx}) should come after 'a' done (idx {a_done_idx})."
        )


# ===========================================================================
# 12. WorkflowCommand — menu
# ===========================================================================


class TestWorkflowMenuPendingInput:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action,expect_pending_input,expect_show,expect_pending_value",
        [
            ("run", True, False, "/workflow run test-wf"),
            ("run-bg", True, False, "/workflow run-bg test-wf"),
            ("show", False, True, None),
            ("status", True, False, "/workflow status test-wf"),
            ("back", False, False, None),
        ],
    )
    async def test_menu_action(
        self, action, expect_pending_input, expect_show, expect_pending_value
    ):
        """Menu actions: run sets pending input, show displays details, back returns."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import command as wf_command

        wf = Workflow(name="test-wf", nodes=[Node(id="a", task="Do stuff")])
        registry = MagicMock()
        registry.list.return_value = [wf]
        registry.get.return_value = wf

        app = MagicMock()
        app.workflow_registry = registry
        app.wf_renderer = MagicMock()
        app.wf_renderer.show.return_value = "tree view"
        display = MagicMock()
        ctx = _make_ctx(app=app, display=display)

        cmd = wf_command
        with patch(
            "mocode.app.cli.commands.workflow.select",
            new_callable=AsyncMock,
        ) as mock_select:
            mock_select.side_effect = ["test-wf", action]
            result = await cmd.menu(ctx, cmd)

        assert result == CommandResult.CONTINUE
        if expect_pending_input:
            display.set_pending_input.assert_called_once_with(expect_pending_value)
        else:
            display.set_pending_input.assert_not_called()
        if expect_show:
            app.wf_renderer.show.assert_called_once_with(wf)
        else:
            app.wf_renderer.show.assert_not_called()


# ===========================================================================
# 13. WorkflowCommand — run, status, result, runs, run-bg
# ===========================================================================


class TestWorkflowCommandRun:
    """WorkflowCommand — run, status, result, runs, run-bg subcommands."""

    @pytest.mark.asyncio
    async def test_run_creates_store_record(self):
        """_run creates a store record and passes run_id + run_store to DAGRunner."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import command as wf_command

        wf = Workflow(name="persist-wf", nodes=[Node(id="a", task="Do stuff")])
        registry = MagicMock()
        registry.get.return_value = wf

        app = MagicMock()
        app.workflow_registry = registry
        display = MagicMock()
        ctx = _make_ctx(app=app, display=display, args="run persist-wf")
        cmd = wf_command

        with (
            patch("mocode.app.cli.commands.workflow.WorkflowRunStore") as MockStore,
            patch("mocode.app.cli.commands.workflow.DAGRunner") as MockRunner,
        ):
            mock_store = MagicMock()
            mock_store.create.return_value = "wf_test123"
            MockStore.return_value = mock_store
            mock_runner = AsyncMock()
            mock_runner.run = AsyncMock(return_value=[
                NodeResult(node_id="a", task="Do stuff", output="ok", exit_code=0, duration=1.0)
            ])
            MockRunner.return_value = mock_runner
            result = await cmd.run(ctx)

        assert result == CommandResult.CONTINUE
        mock_store.create.assert_called_once_with(
            workflow_name="persist-wf", workflow_path=str(wf.path), args={},
        )
        _, kwargs = MockRunner.call_args
        assert kwargs.get("run_id") == "wf_test123"
        assert kwargs.get("run_store") is mock_store

    @pytest.mark.asyncio
    async def test_run_bg_returns_immediately(self):
        """--bg starts an asyncio task and returns immediately."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import command as wf_command

        wf = Workflow(name="bg-wf", nodes=[Node(id="a", task="Do stuff")])
        registry = MagicMock()
        registry.get.return_value = wf

        app = MagicMock()
        app.workflow_registry = registry
        display = MagicMock()
        ctx = _make_ctx(app=app, display=display, args="run-bg bg-wf key=val")
        cmd = wf_command

        with (
            patch("mocode.app.cli.commands.workflow.WorkflowRunStore") as MockStore,
            patch("mocode.app.cli.commands.workflow.DAGRunner") as MockRunner,
        ):
            mock_store = MagicMock()
            mock_store.create.return_value = "wf_bg123"
            MockStore.return_value = mock_store
            mock_runner_instance = MagicMock()
            mock_runner_instance.run = AsyncMock(return_value=[])
            MockRunner.return_value = mock_runner_instance
            result = await cmd.run(ctx)

        assert result == CommandResult.CONTINUE
        mock_store.create.assert_called_once_with(
            workflow_name="bg-wf", workflow_path=str(wf.path), args={"key": "val"},
        )
        mock_runner_instance.run.assert_not_called()
        info_text = display.info.call_args[0][0]
        assert "bg-wf" in info_text
        assert "wf_bg123" in info_text
        assert "background" in info_text

    @pytest.mark.asyncio
    async def test_run_fg_blocks(self):
        """Without --bg, _run blocks until completion."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import command as wf_command

        wf = Workflow(name="fg-wf", nodes=[Node(id="a", task="Do stuff")])
        registry = MagicMock()
        registry.get.return_value = wf

        app = MagicMock()
        app.workflow_registry = registry
        display = MagicMock()
        ctx = _make_ctx(app=app, display=display, args="run fg-wf")
        cmd = wf_command

        with (
            patch("mocode.app.cli.commands.workflow.WorkflowRunStore") as MockStore,
            patch("mocode.app.cli.commands.workflow.DAGRunner") as MockRunner,
        ):
            mock_store = MagicMock()
            mock_store.create.return_value = "wf_fg123"
            MockStore.return_value = mock_store
            mock_runner_instance = AsyncMock()
            mock_runner_instance.run = AsyncMock(return_value=[
                NodeResult(node_id="a", task="Do stuff", output="ok", exit_code=0, duration=1.0)
            ])
            MockRunner.return_value = mock_runner_instance
            result = await cmd.run(ctx)

        assert result == CommandResult.CONTINUE
        mock_runner_instance.run.assert_called_once()
        app.wf_renderer.summary.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("subcommand,args_str", [
        ("status", "status wf_abc123"),
        ("result", "result wf_abc123"),
    ])
    async def test_status_and_result_with_run_id(self, subcommand, args_str):
        """status/result subcommands with a run ID display run data."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import command as wf_command

        app = MagicMock()
        display = MagicMock()
        ctx = _make_ctx(app=app, display=display, args=args_str)
        cmd = wf_command

        with patch("mocode.app.cli.commands.workflow.WorkflowRunStore") as MockStore:
            mock_store = MagicMock()
            mock_store.resolve_run_id.return_value = "wf_abc123"
            mock_store.load.return_value = {
                "run_id": "wf_abc123", "workflow_name": "my-workflow",
                "status": "completed", "wall_duration": 30.5,
                "started_at": "2025-01-01T00:00:00",
                "finished_at": "2025-01-01T00:01:00",
                "results": [{
                    "node_id": "a", "task": "Do thing", "output": "ok",
                    "exit_code": 0, "duration": 30.5, "error": None,
                    "status": "done", "iteration": 1,
                }],
            }
            MockStore.return_value = mock_store
            result = await cmd.run(ctx)

        assert result == CommandResult.CONTINUE
        assert "wf_abc123" in (display.info.call_args[0][0] if display.info.called
                               else display.print.call_args[0][0])
        assert "completed" in (display.info.call_args[0][0] if display.info.called
                               else display.print.call_args[0][0])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("subcommand", ["status", "result", "runs"])
    async def test_subcommand_no_runs(self, subcommand):
        """status/result/runs with no data shows a warning."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import command as wf_command

        app = MagicMock()
        display = MagicMock()
        ctx = _make_ctx(app=app, display=display, args=subcommand)
        cmd = wf_command

        with patch("mocode.app.cli.commands.workflow.WorkflowRunStore") as MockStore:
            mock_store = MagicMock()
            mock_store.resolve_run_id.return_value = None
            mock_store.list_recent.return_value = []
            MockStore.return_value = mock_store
            result = await cmd.run(ctx)

        assert result == CommandResult.CONTINUE
        display.warn.assert_called()

    @pytest.mark.asyncio
    async def test_runs_with_records(self):
        """runs subcommand lists recent runs."""
        from mocode.app.cli.commands import CommandResult
        from mocode.app.cli.commands.workflow import command as wf_command

        app = MagicMock()
        display = MagicMock()
        ctx = _make_ctx(app=app, display=display, args="runs")
        cmd = wf_command

        with patch("mocode.app.cli.commands.workflow.WorkflowRunStore") as MockStore:
            mock_store = MagicMock()
            mock_store.list_recent.return_value = [
                {"run_id": "wf_aaa", "workflow_name": "wf1",
                 "status": "completed", "started_at": "2025-01-01T00:00:00"},
                {"run_id": "wf_bbb", "workflow_name": "wf2",
                 "status": "running", "started_at": "2025-01-01T00:05:00", "pid": 12345},
            ]
            mock_store.is_alive.return_value = True
            MockStore.return_value = mock_store
            result = await cmd.run(ctx)

        assert result == CommandResult.CONTINUE
        info_text = display.info.call_args[0][0]
        assert "wf_aaa" in info_text
        assert "wf_bbb" in info_text
        assert "completed" in info_text
        assert "running" in info_text


# ===========================================================================
# 14. parse_args — positional mapping, key=value, defaults
# ===========================================================================


class TestParseArgs:
    def test_positional_mapping(self):
        params = [
            ParamDef(name="direction", required=True),
            ParamDef(name="requirement", required=True),
        ]
        result = parse_args(params, ["做CLI", "好用"])
        assert result == {"direction": "做CLI", "requirement": "好用"}

    def test_positional_with_default(self):
        params = [
            ParamDef(name="direction", required=True),
            ParamDef(name="requirement", required=True),
            ParamDef(name="depth", default="deep", required=False),
        ]
        result = parse_args(params, ["做CLI", "好用"])
        assert result == {"direction": "做CLI", "requirement": "好用", "depth": "deep"}

    def test_positional_override_default(self):
        params = [
            ParamDef(name="direction", required=True),
            ParamDef(name="requirement", required=True),
            ParamDef(name="depth", default="deep", required=False),
        ]
        result = parse_args(params, ["做CLI", "好用", "shallow"])
        assert result == {"direction": "做CLI", "requirement": "好用", "depth": "shallow"}

    def test_key_value_override(self):
        params = [
            ParamDef(name="direction", required=True),
            ParamDef(name="requirement", required=True),
        ]
        result = parse_args(params, ["做CLI", "requirement=自定义"])
        assert result == {"direction": "做CLI", "requirement": "自定义"}

    def test_missing_required_raises(self):
        params = [
            ParamDef(name="direction", required=True),
            ParamDef(name="requirement", required=True),
        ]
        with pytest.raises(ValueError, match="Missing required parameter: requirement"):
            parse_args(params, ["做CLI"])

    def test_extra_positional_ignored(self):
        params = [ParamDef(name="a", required=True)]
        result = parse_args(params, ["val1", "extra"])
        assert result == {"a": "val1"}

    def test_empty_params_empty_args(self):
        result = parse_args([], ["anything=123"])
        assert result == {"anything": "123"}


# ===========================================================================
# 15. Workflow params — YAML parsing
# ===========================================================================


class TestWorkflowParams:
    def test_params_from_yaml_string(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "params.yaml", {
            "name": "plan",
            "params": ["direction", "requirement"],
            "nodes": [
                {"id": "t", "task": "方向: {direction}, 要求: {requirement}"},
            ],
        })
        wf = Workflow.from_yaml(path)
        assert len(wf.params) == 2
        assert wf.params[0].name == "direction"
        assert wf.params[0].required is True
        assert wf.params[1].name == "requirement"

    def test_params_from_yaml_with_default(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "params_default.yaml", {
            "name": "plan",
            "params": [
                "direction",
                {"depth": "deep"},
            ],
            "nodes": [
                {"id": "t", "task": "方向: {direction}, 深度: {depth}"},
            ],
        })
        wf = Workflow.from_yaml(path)
        assert len(wf.params) == 2
        assert wf.params[0].name == "direction"
        assert wf.params[0].required is True
        assert wf.params[1].name == "depth"
        assert wf.params[1].default == "deep"
        assert wf.params[1].required is False

    def test_params_from_yaml_explicit_dict(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "params_explicit.yaml", {
            "name": "plan",
            "params": [
                {"name": "direction"},
                {"name": "depth", "default": "deep"},
            ],
            "nodes": [
                {"id": "t", "task": "方向: {direction}, 深度: {depth}"},
            ],
        })
        wf = Workflow.from_yaml(path)
        assert wf.params[0].name == "direction"
        assert wf.params[0].required is True
        assert wf.params[1].name == "depth"
        assert wf.params[1].default == "deep"
        assert wf.params[1].required is False


# ===========================================================================
# 16. Map node — unified {item} template syntax regression
# ===========================================================================


class TestMapTemplateUnified:
    @pytest.mark.asyncio
    async def test_single_brace_item_in_map(self):
        """Map node uses {item} (single brace) and it works via fill_template."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="gen", task="Generate"),
                Node(
                    id="search", type="map",
                    items="{nodes.gen.output}", item_key="kw",
                    task="Search for {kw}", depends=["gen"],
                ),
            ],
        )
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())

        async def _mock_exec(node_id, task, context_header=None):
            return NodeResult(node_id=node_id, task=task,
                              output="alpha\nbeta" if node_id == "gen" else f"r-{node_id}",
                              exit_code=0, duration=0.1)

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

        # Verify child tasks got the item value substituted
        child_results = [r for r in results if "::" in r.node_id]
        assert len(child_results) == 2
        assert child_results[0].task == "Search for alpha"
        assert child_results[1].task == "Search for beta"

    @pytest.mark.asyncio
    async def test_mixed_item_and_node_ref_in_map(self):
        """Map task can mix {item} and {nodes.X.output} in the same template."""
        wf = Workflow(
            name="t",
            nodes=[
                Node(id="ctx", task="context data"),
                Node(id="gen", task="items source"),
                Node(
                    id="m", type="map",
                    items="{nodes.gen.output}", item_key="item",
                    task="Use {nodes.ctx.output} with {item}", depends=["ctx", "gen"],
                ),
            ],
        )
        runner = DAGRunner(wf, parent_agent=_make_mock_agent())

        async def _mock_exec(node_id, task, context_header=None):
            return NodeResult(node_id=node_id, task=task,
                              output="context-info" if node_id == "ctx" else ("x\ny" if node_id == "gen" else f"r"),
                              exit_code=0, duration=0.1)

        with patch.object(runner, "_exec_node", side_effect=_mock_exec):
            results = await runner.run()

        child_results = [r for r in results if "::" in r.node_id]
        assert child_results[0].task == "Use context-info with x"
        assert child_results[1].task == "Use context-info with y"


# ===========================================================================
# 17. RunState — unified skipped dict
# ===========================================================================


class TestRunStateSkipped:
    def test_skip_records_reason(self):
        """Unified skipped dict stores node_id → reason."""
        wf = _simple_linear_wf(2)
        state = RunState.from_workflow(wf)
        state.skip("n1", "not activated by router")
        assert "n1" in state.skipped
        assert state.skipped["n1"] == "not activated by router"

    def test_skip_idempotent(self):
        """Calling skip twice on same node keeps first reason."""
        wf = _simple_linear_wf(2)
        state = RunState.from_workflow(wf)
        state.skip("n1", "first reason")
        state.skip("n1", "second reason")
        assert state.skipped["n1"] == "first reason"

    def test_activate_removes_from_skipped(self):
        """Activating a skipped node removes it from skipped dict."""
        wf = _simple_linear_wf(2)
        state = RunState.from_workflow(wf)
        state.skip("n1", "not activated")
        assert "n1" in state.skipped
        state.activate("n1")
        assert "n1" not in state.skipped


# ===========================================================================
# 18. _WorkflowNodeHook — event emission
# ===========================================================================


class TestWorkflowNodeHook:
    @pytest.mark.asyncio
    async def test_on_tool_complete_emits_event(self):
        """Hook emits NodeToolCallEvent on tool_complete."""
        from mocode.app.workflow.runner import _WorkflowNodeHook
        from mocode.core.hook import AgentHookContext
        from mocode.core.provider import Response

        events = []
        hook = _WorkflowNodeHook("my-node", events.append)

        # Simulate on_response with tool calls
        tc = MagicMock()
        tc.name = "bash"
        tc.arguments = '{"command": "ls"}'
        ctx = AgentHookContext()
        ctx.response = Response(content=None, tool_calls=[tc])
        await hook.on_response(ctx)

        # Simulate on_tool_start
        ctx_tool = AgentHookContext()
        ctx_tool.tool_name = "bash"
        ctx_tool.tool_args = {"command": "ls"}
        ctx_tool.tool_call_id = "call_1"
        await hook.on_tool_start(ctx_tool)

        # Simulate on_tool_complete
        ctx_tool.tool_result = "file1.py"
        await hook.on_tool_complete(ctx_tool)

        assert len(events) == 1
        assert isinstance(events[0], NodeToolCallEvent)
        assert events[0].node_id == "my-node"
        assert events[0].tool_name == "bash"
        assert events[0].tool_args == {"command": "ls"}
        assert events[0].error is None
        assert events[0].elapsed >= 0

    @pytest.mark.asyncio
    async def test_after_tools_emits_batch_done(self):
        """Hook emits NodeToolBatchDoneEvent after_tools."""
        from mocode.app.workflow.runner import _WorkflowNodeHook
        from mocode.core.hook import AgentHookContext
        from mocode.core.provider import Response

        events = []
        hook = _WorkflowNodeHook("n1", events.append)

        # Simulate on_response with tool calls
        tc = MagicMock()
        tc.name = "read"
        tc.arguments = '{"path": "test.py"}'
        ctx = AgentHookContext()
        ctx.response = Response(content=None, tool_calls=[tc])
        await hook.on_response(ctx)

        # Simulate on_tool_start + on_tool_complete
        ctx_tool = AgentHookContext()
        ctx_tool.tool_name = "read"
        ctx_tool.tool_args = {"path": "test.py"}
        ctx_tool.tool_call_id = "call_1"
        await hook.on_tool_start(ctx_tool)
        ctx_tool.tool_result = "contents"
        await hook.on_tool_complete(ctx_tool)

        # Simulate after_tools
        await hook.after_tools(ctx)

        batch_events = [e for e in events if isinstance(e, NodeToolBatchDoneEvent)]
        assert len(batch_events) == 1
        assert batch_events[0].node_id == "n1"
        assert len(batch_events[0].groups) >= 1

    @pytest.mark.asyncio
    async def test_error_captured_in_event(self):
        """Hook captures tool error in NodeToolCallEvent."""
        from mocode.app.workflow.runner import _WorkflowNodeHook
        from mocode.core.hook import AgentHookContext

        events = []
        hook = _WorkflowNodeHook("n1", events.append)

        ctx_tool = AgentHookContext()
        ctx_tool.tool_name = "bash"
        ctx_tool.tool_args = {"command": "bad"}
        ctx_tool.tool_call_id = "call_1"
        await hook.on_tool_start(ctx_tool)

        ctx_tool.tool_error = "command not found: bad"
        await hook.on_tool_complete(ctx_tool)

        assert events[0].error == "command not found: bad"

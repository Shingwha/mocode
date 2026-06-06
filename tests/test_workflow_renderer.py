"""Tests for WorkflowRenderer — event rendering, DAG tree, and execution summary."""

from __future__ import annotations

from unittest.mock import MagicMock

from mocode.app.cli.workflow_renderer import WorkflowRenderer, _format_route
from mocode.app.cli.spinner import Priority, Truncate
from mocode.app.workflow.events import (
    LoopIterEvent,
    MapFanOutEvent,
    MapItemDoneEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    NodeToolBatchDoneEvent,
    NodeToolCallEvent,
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
)
from mocode.app.workflow.models import NodeResult


def _make_renderer(capture: list | None = None):
    """Create a WorkflowRenderer with a mock Display that captures print output."""
    lines = capture if capture is not None else []
    display = MagicMock()

    def _capture_print(*a, **kw):
        lines.append(" ".join(str(x) for x in a))

    def _capture_render(*a, **kw):
        parts = [str(x) for x in a]
        if kw:
            for k, v in kw.items():
                parts.append(f"{k}={v}")
        lines.append(" ".join(parts))

    display.print = _capture_print
    display.render_line = _capture_render
    display.spinner_set = MagicMock()
    display.spinner_remove = MagicMock()
    return WorkflowRenderer(display), lines


# ── _format_route ──────────────────────────────────────────


class TestFormatRoute:
    def test_match_and_to(self):
        route = MagicMock()
        route.match = "success"
        route.to = ["node_a", "node_b"]
        route.max = 0
        result = _format_route(route)
        assert "/success/" in result
        assert "node_a, node_b" in result

    def test_no_match(self):
        route = MagicMock()
        route.match = ""
        route.to = ["fallback"]
        route.max = 0
        result = _format_route(route)
        assert result == "fallback"

    def test_with_max(self):
        route = MagicMock()
        route.match = ""
        route.to = ["loop"]
        route.max = 3
        result = _format_route(route)
        assert "loop" in result
        assert "(max 3)" in result


# ── Event handlers ─────────────────────────────────────────


class TestOnWaveReady:
    def test_first_wave_no_blank_line(self):
        renderer, lines = _make_renderer()
        event = WaveReadyEvent(wave_idx=0, total_waves=3, node_ids=["a", "b"])
        renderer.handle_event(event)
        assert any("Wave 1/3" in l for l in lines)
        assert any("a, b" in l for l in lines)

    def test_later_wave_adds_blank_line(self):
        renderer, lines = _make_renderer()
        event = WaveReadyEvent(wave_idx=1, total_waves=3, node_ids=["c"])
        renderer.handle_event(event)
        assert lines[0] == ""  # blank separator

    def test_many_nodes_truncated(self):
        renderer, lines = _make_renderer()
        event = WaveReadyEvent(
            wave_idx=0, total_waves=1,
            node_ids=["a", "b", "c", "d", "e"],
        )
        renderer.handle_event(event)
        assert any("+2 more" in l for l in lines)


class TestOnRouterCondition:
    def test_matched_router(self):
        renderer, lines = _make_renderer()
        event = RouterConditionEvent(
            router_id="r1", matched=True, branch="ok",
            targets=["node_a"], wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("node_a" in l for l in lines)

    def test_unmatched_router(self):
        renderer, lines = _make_renderer()
        event = RouterConditionEvent(
            router_id="r1", matched=False, branch="",
            targets=[], wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("no match" in l for l in lines)


class TestOnNodeSkipped:
    def test_known_reason(self):
        renderer, lines = _make_renderer()
        event = NodeSkippedEvent(
            node_id="x", reason="not activated by router", wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("routed elsewhere" in l for l in lines)

    def test_unknown_reason(self):
        renderer, lines = _make_renderer()
        event = NodeSkippedEvent(
            node_id="x", reason="something else", wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("something else" in l for l in lines)


class TestOnNodeStart:
    def test_sets_spinner_tag(self):
        renderer, _ = _make_renderer()
        event = NodeStartEvent(node_id="analyze", description="Analyze code")
        renderer.handle_event(event)
        renderer._d.spinner_set.assert_any_call(
            "wf_node", "analyze", priority=Priority.HIGH, truncate=Truncate.NONE,
        )

    def test_with_description_sets_thinking(self):
        renderer, _ = _make_renderer()
        event = NodeStartEvent(node_id="a", description="Do stuff")
        renderer.handle_event(event)
        renderer._d.spinner_set.assert_any_call(
            "wf_thinking", "Thinking", priority=Priority.NORMAL, truncate=Truncate.TAIL,
        )

    def test_without_description_sets_thinking(self):
        renderer, _ = _make_renderer()
        event = NodeStartEvent(node_id="a", description="")
        renderer.handle_event(event)
        renderer._d.spinner_set.assert_any_call(
            "wf_thinking", "Thinking", priority=Priority.NORMAL, truncate=Truncate.TAIL,
        )


class TestOnNodeDone:
    def test_success_prints_square(self):
        renderer, lines = _make_renderer()
        result = NodeResult(
            node_id="a", task="Do stuff", output="ok",
            exit_code=0, duration=1.5,
        )
        event = NodeDoneEvent(
            node_id="a", result=result, description="Do stuff", wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("■" in l and "a" in l for l in lines)

    def test_failure_prints_square_red(self):
        renderer, lines = _make_renderer()
        result = NodeResult(
            node_id="a", task="Do stuff", output="",
            exit_code=1, duration=0.5,
        )
        event = NodeDoneEvent(
            node_id="a", result=result, description="", wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("■" in l for l in lines)

    def test_clears_spinner_segments(self):
        renderer, _ = _make_renderer()
        result = NodeResult(
            node_id="a", task="Do stuff", output="ok",
            exit_code=0, duration=1.0,
        )
        event = NodeDoneEvent(
            node_id="a", result=result, description="", wave_idx=0,
        )
        renderer.handle_event(event)
        renderer._d.spinner_remove.assert_any_call("wf_node")
        renderer._d.spinner_remove.assert_any_call("wf_thinking")
        renderer._d.spinner_remove.assert_any_call("wf_tools_tag")
        renderer._d.spinner_remove.assert_any_call("wf_tools_detail")


class TestOnLoopIter:
    def test_prints_iteration(self):
        renderer, lines = _make_renderer()
        result = NodeResult(
            node_id="loop", task="Retry", output="ok",
            exit_code=0, duration=2.0,
        )
        event = LoopIterEvent(
            node_id="loop", iteration=3, max_iter=5,
            result=result, description="Retry",
        )
        renderer.handle_event(event)
        assert any("3/5" in l for l in lines)


class TestOnMapFanOut:
    def test_prints_item_count(self):
        renderer, lines = _make_renderer()
        event = MapFanOutEvent(map_id="m1", item_count=5, wave_idx=0)
        renderer.handle_event(event)
        assert any("5 items" in l for l in lines)


class TestOnMapItemDone:
    def test_prints_item_index(self):
        renderer, lines = _make_renderer()
        event = MapItemDoneEvent(
            map_id="m1", item_index=2, total_count=5,
            item_value="item_c", duration=1.0,
        )
        renderer.handle_event(event)
        assert any("3/5" in l for l in lines)


class TestOnProgress:
    def test_with_node_id_sets_spinner(self):
        renderer, _ = _make_renderer()
        event = ProgressEvent(message="working", node_id="a", detail="Analyzing")
        renderer.handle_event(event)
        renderer._d.spinner_set.assert_any_call(
            "wf_node", "a", priority=Priority.HIGH, truncate=Truncate.NONE,
        )

    def test_without_node_id_only_sets_thinking(self):
        renderer, _ = _make_renderer()
        event = ProgressEvent(message="Starting", node_id="", detail="")
        renderer.handle_event(event)
        renderer._d.spinner_set.assert_called_with(
            "wf_thinking", "Starting", priority=Priority.NORMAL, truncate=Truncate.TAIL,
        )


# ── Lifecycle rendering ────────────────────────────────────


class TestWorkflowStart:
    def test_prints_header(self):
        renderer, lines = _make_renderer()
        wf = MagicMock()
        wf.name = "my-workflow"
        wf.total_nodes.return_value = 5
        renderer.start(wf)
        assert any("my-workflow" in l for l in lines)
        assert any("5 nodes" in l for l in lines)


class TestWorkflowSummary:
    def test_all_passed(self):
        renderer, lines = _make_renderer()
        wf = MagicMock()
        wf.name = "test-wf"
        r1 = MagicMock(exit_code=0, status="done", output="ok", error=None)
        renderer.start(wf)
        renderer.summary(wf, [r1])
        assert any("1 passed" in l for l in lines)

    def test_mixed_results(self):
        renderer, lines = _make_renderer()
        wf = MagicMock()
        wf.name = "test-wf"
        r_ok = MagicMock(exit_code=0, status="done", output="ok", error=None)
        r_fail = MagicMock(exit_code=1, status="done", output="", error="boom")
        r_skip = MagicMock(exit_code=0, status="skipped", output="", error=None)
        renderer.start(wf)
        renderer.summary(wf, [r_ok, r_fail, r_skip])
        summary_lines = [l for l in lines if "passed" in l and "failed" in l]
        assert len(summary_lines) >= 1
        assert any("skipped" in l for l in summary_lines)


# ── Static views ───────────────────────────────────────────


class TestWorkflowShow:
    def test_single_task_node(self):
        renderer, _ = _make_renderer()
        node = MagicMock()
        node.id = "a"
        node.type = "task"
        node.description = "Do stuff"
        node.task = "Do stuff"
        node.routes = []
        wf = MagicMock()
        wf.name = "simple"
        wf.description = "A simple workflow"
        wf.total_nodes.return_value = 1
        wf.node_map = {"a": node}
        wf.dependents = {}
        wf.root_nodes = [node]
        result = renderer.show(wf)
        assert "simple" in result
        assert "a" in result
        assert "Do stuff" in result

    def test_router_node_shows_routes(self):
        renderer, _ = _make_renderer()
        route = MagicMock()
        route.match = "ok"
        route.to = ["next"]
        route.max = 0
        router = MagicMock()
        router.id = "r"
        router.type = "router"
        router.description = ""
        router.task = ""
        router.routes = [route]
        wf = MagicMock()
        wf.name = "routed"
        wf.description = ""
        wf.total_nodes.return_value = 1
        wf.node_map = {"r": router}
        wf.dependents = {}
        wf.root_nodes = [router]
        result = renderer.show(wf)
        assert "router" in result
        assert "next" in result


class TestWorkflowList:
    def test_lists_workflows(self):
        renderer, _ = _make_renderer()
        wf1 = MagicMock()
        wf1.name = "alpha"
        wf1.description = "First workflow"
        wf2 = MagicMock()
        wf2.name = "beta"
        wf2.description = ""
        result = renderer.list_workflows([wf1, wf2])
        assert "alpha" in result
        assert "First workflow" in result
        assert "beta" in result


# ── New tool call event handlers ────────────────────────────


class TestOnToolCall:
    def test_single_tool_updates_spinner(self):
        renderer, _ = _make_renderer()
        event = NodeToolCallEvent(
            node_id="diff", tool_name="bash",
            tool_args={"command": "git log --oneline -20"},
            elapsed=0.8,
        )
        renderer.handle_event(event)
        # Should remove thinking and set tools spinner segments
        renderer._d.spinner_remove.assert_any_call("wf_thinking")
        renderer._d.spinner_set.assert_any_call(
            "wf_tools_tag", "diff: running 1 tool",
            priority=Priority.HIGH, truncate=Truncate.TAIL,
        )
        renderer._d.spinner_set.assert_any_call(
            "wf_tools_detail", "bash(git log --oneline -20)",
            priority=Priority.LOW, truncate=Truncate.MIDDLE,
        )

    def test_multiple_tools_accumulate(self):
        renderer, _ = _make_renderer()
        renderer.handle_event(NodeToolCallEvent(
            node_id="build", tool_name="read",
            tool_args={"path": "a.txt"}, elapsed=0.1,
        ))
        renderer.handle_event(NodeToolCallEvent(
            node_id="build", tool_name="read",
            tool_args={"path": "b.txt"}, elapsed=0.1,
        ))
        renderer.handle_event(NodeToolCallEvent(
            node_id="build", tool_name="bash",
            tool_args={"command": "make"}, elapsed=0.5,
        ))
        # Should show running 3 tools with deduplicated detail
        renderer._d.spinner_set.assert_any_call(
            "wf_tools_tag", "build: running 3 tools",
            priority=Priority.HIGH, truncate=Truncate.TAIL,
        )
        renderer._d.spinner_set.assert_any_call(
            "wf_tools_detail", "read×2, bash",
            priority=Priority.LOW, truncate=Truncate.MIDDLE,
        )


class TestOnToolBatchDone:
    def test_clears_tool_spinner_segments(self):
        renderer, _ = _make_renderer()
        event = NodeToolBatchDoneEvent(
            node_id="diff",
            groups=[("bash", ["git log"])],
            errors={}, elapsed={"bash": 0.8},
        )
        renderer.handle_event(event)
        renderer._d.spinner_remove.assert_any_call("wf_tools_tag")
        renderer._d.spinner_remove.assert_any_call("wf_tools_detail")
        renderer._d.spinner_set.assert_any_call(
            "wf_thinking", "Thinking", priority=Priority.NORMAL, truncate=Truncate.TAIL,
        )


class TestHandleEventDispatch:
    def test_dispatches_tool_call_event(self):
        renderer, _ = _make_renderer()
        event = NodeToolCallEvent(
            node_id="n1", tool_name="read",
            tool_args={"path": "test.py"}, elapsed=0.3,
        )
        renderer.handle_event(event)
        # Tool calls update spinner, not print
        renderer._d.spinner_set.assert_any_call(
            "wf_tools_tag", "n1: running 1 tool",
            priority=Priority.HIGH, truncate=Truncate.TAIL,
        )

    def test_dispatches_tool_batch_done_event(self):
        renderer, _ = _make_renderer()
        event = NodeToolBatchDoneEvent(
            node_id="n1", groups=[("read", ["test.py"])],
            errors={}, elapsed={"read": 0.3},
        )
        renderer.handle_event(event)
        renderer._d.spinner_remove.assert_any_call("wf_tools_tag")

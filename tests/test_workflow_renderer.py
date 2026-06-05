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
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
)
from mocode.app.workflow.models import NodeResult


def _make_renderer(capture: list | None = None):
    """Create a WorkflowRenderer with a mock Display that captures _print output."""
    lines = capture if capture is not None else []
    display = MagicMock()
    display._print = lambda *a, **kw: lines.append(" ".join(str(x) for x in a))
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
            "wf_tag", "analyze", priority=Priority.NORMAL, truncate=Truncate.TAIL,
        )

    def test_with_description_sets_detail(self):
        renderer, _ = _make_renderer()
        event = NodeStartEvent(node_id="a", description="Do stuff")
        renderer.handle_event(event)
        renderer._d.spinner_set.assert_any_call(
            "wf_detail", "Do stuff", priority=Priority.LOW, truncate=Truncate.MIDDLE,
        )

    def test_without_description_removes_detail(self):
        renderer, _ = _make_renderer()
        event = NodeStartEvent(node_id="a", description="")
        renderer.handle_event(event)
        renderer._d.spinner_remove.assert_called_with("wf_detail")


class TestOnNodeDone:
    def test_success_prints_checkmark(self):
        renderer, lines = _make_renderer()
        result = NodeResult(
            node_id="a", task="Do stuff", output="ok",
            exit_code=0, duration=1.5,
        )
        event = NodeDoneEvent(
            node_id="a", result=result, description="Do stuff", wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("✓" in l and "a" in l for l in lines)

    def test_failure_prints_cross(self):
        renderer, lines = _make_renderer()
        result = NodeResult(
            node_id="a", task="Do stuff", output="",
            exit_code=1, duration=0.5,
        )
        event = NodeDoneEvent(
            node_id="a", result=result, description="", wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("✗" in l for l in lines)

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
        renderer._d.spinner_remove.assert_any_call("wf_tag")
        renderer._d.spinner_remove.assert_any_call("wf_detail")


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
            "wf_tag", "a", priority=Priority.NORMAL, truncate=Truncate.TAIL,
        )

    def test_without_node_id_only_sets_detail(self):
        renderer, _ = _make_renderer()
        event = ProgressEvent(message="Starting", node_id="", detail="")
        renderer.handle_event(event)
        renderer._d.spinner_set.assert_called_with(
            "wf_detail", "Starting", priority=Priority.LOW, truncate=Truncate.MIDDLE,
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

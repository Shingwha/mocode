"""Tests for WorkflowRenderer — event rendering, DAG tree, and execution summary."""

from __future__ import annotations

from unittest.mock import MagicMock

from mocode.app.cli.workflow_renderer import WorkflowRenderer, _format_route
from mocode.app.cli.spinner import Priority, Truncate
from mocode.app.workflow.events import (
    NodeDoneEvent,
    NodeToolBatchDoneEvent,
    NodeToolCallEvent,
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


# ── Core event rendering ──────────────────────────────────


class TestOnWaveReady:
    def test_first_wave_no_blank_line(self):
        renderer, lines = _make_renderer()
        event = WaveReadyEvent(wave_idx=0, total_waves=3, node_ids=["a", "b"])
        renderer.handle_event(event)
        assert any("Wave 1/3" in l for l in lines)
        assert any("a, b" in l for l in lines)


class TestOnRouterCondition:
    def test_matched_router(self):
        renderer, lines = _make_renderer()
        event = RouterConditionEvent(
            router_id="r1", matched=True, branch="ok",
            targets=["node_a"], wave_idx=0,
        )
        renderer.handle_event(event)
        assert any("node_a" in l for l in lines)


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


# ── DAG tree and summary ──────────────────────────────────


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


class TestWorkflowSummary:
    def test_all_passed(self):
        renderer, lines = _make_renderer()
        wf = MagicMock()
        wf.name = "test-wf"
        r1 = MagicMock(exit_code=0, status="done", output="ok", error=None,
                       tool_calls=0, prompt_tokens=0, completion_tokens=0)
        renderer.start(wf)
        renderer.summary(wf, [r1])
        assert any("1 passed" in l for l in lines)


class TestRunDetail:
    def test_completed_run(self):
        renderer, _ = _make_renderer()
        record = {
            "run_id": "wf_abc", "workflow_name": "my-wf",
            "status": "completed",
            "started_at": "2025-01-01T00:00:00",
            "finished_at": "2025-01-01T00:01:00",
            "wall_duration": 60.0,
            "results": [{
                "node_id": "a", "task": "Do thing", "output": "ok",
                "exit_code": 0, "duration": 60.0, "error": None,
                "status": "done",
            }],
        }
        result = renderer.run_detail(record, "completed")
        assert "wf_abc" in result
        assert "completed" in result
        assert "60.0s" in result
        assert "Do thing" in result

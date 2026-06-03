"""Tests for workflow engine — models, fill_template, registry, runner, command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.app.workflow import (
    GotoRule,
    Lane,
    Phase,
    Step,
    StepResult,
    Workflow,
    WorkflowRegistry,
    fill_template,
)
from mocode.app.workflow.runner import WorkflowRunner


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


# ---------------------------------------------------------------------------
# 7.1 Model tests
# ---------------------------------------------------------------------------


class TestGotoRule:
    def test_from_dict_full(self):
        rule = GotoRule.from_dict({"match": "error", "to": "phase.retry", "max": 3})
        assert rule.match == "error"
        assert rule.to == "phase.retry"
        assert rule.max == 3

    def test_from_dict_default(self):
        rule = GotoRule.from_dict({"to": "next"})
        assert rule.match is None
        assert rule.to == "next"
        assert rule.max == 0

    def test_from_dict_empty(self):
        rule = GotoRule.from_dict({})
        assert rule.match is None
        assert rule.to == "next"
        assert rule.max == 0

    def test_default_fallback(self):
        rule = GotoRule()
        assert rule.match is None
        assert rule.to == "next"
        assert rule.max == 0


class TestStepWithGoto:
    def test_from_dict_basic(self):
        step = Step.from_dict({"id": "web", "task": "Search the web"})
        assert step.id == "web"
        assert step.task == "Search the web"
        assert step.goto == []

    def test_from_dict_with_goto(self):
        step = Step.from_dict({
            "id": "check",
            "task": "Check status",
            "goto": [
                {"match": "error", "to": "phase.retry", "max": 3},
                {"to": "next"},
            ],
        })
        assert step.id == "check"
        assert len(step.goto) == 2
        assert step.goto[0].match == "error"
        assert step.goto[0].to == "phase.retry"
        assert step.goto[1].to == "next"

    def test_from_dict_defaults(self):
        step = Step.from_dict({})
        assert step.id == ""
        assert step.task == ""
        assert step.goto == []


class TestLane:
    def test_from_dict_basic(self):
        lane = Lane.from_dict({
            "id": "web",
            "name": "Web Search",
            "steps": [{"id": "s1", "task": "Search"}],
        })
        assert lane.id == "web"
        assert lane.name == "Web Search"
        assert len(lane.steps) == 1
        assert lane.steps[0].id == "s1"

    def test_from_dict_defaults(self):
        lane = Lane.from_dict({})
        assert lane.id == ""
        assert lane.name == ""
        assert lane.steps == []


class TestPhaseModel:
    def test_phase_with_steps(self):
        phase = Phase.from_dict({
            "id": "research",
            "name": "Research",
            "steps": [{"id": "web", "task": "Search"}],
        })
        assert phase.id == "research"
        assert phase.name == "Research"
        assert len(phase.steps) == 1
        assert phase.lanes == []
        assert phase.max_iterations == 0
        assert phase.goto == []

    def test_phase_with_lanes(self):
        phase = Phase.from_dict({
            "id": "review",
            "name": "Review",
            "lanes": [
                {
                    "id": "correctness",
                    "steps": [{"id": "check", "task": "Check correctness"}],
                },
            ],
        })
        assert len(phase.lanes) == 1
        assert phase.steps == []  # mutually exclusive
        assert phase.lanes[0].id == "correctness"

    def test_phase_steps_lanes_mutually_exclusive(self):
        """lanes wins when both are present."""
        phase = Phase.from_dict({
            "name": "Test",
            "steps": [{"task": "step1"}],
            "lanes": [{"id": "l1", "steps": [{"task": "lane task"}]}],
        })
        assert len(phase.lanes) == 1
        assert len(phase.steps) == 0

    def test_phase_with_goto(self):
        phase = Phase.from_dict({
            "name": "P1",
            "steps": [{"task": "Do it"}],
            "goto": [
                {"match": "done", "to": "phase.next"},
                {"to": "end"},
            ],
        })
        assert len(phase.goto) == 2
        assert phase.goto[0].match == "done"
        assert phase.goto[1].to == "end"

    def test_phase_max_iterations(self):
        phase = Phase.from_dict({
            "name": "Loop",
            "steps": [{"task": "Retry"}],
            "max_iterations": 5,
        })
        assert phase.max_iterations == 5

    def test_from_dict_defaults(self):
        phase = Phase.from_dict({"name": "Test"})
        assert phase.id == ""
        assert phase.steps == []
        assert phase.lanes == []
        assert phase.max_iterations == 0
        assert phase.goto == []


class TestWorkflowModel:
    def test_workflow_max_iterations(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "test.yaml",
            {
                "name": "my-wf",
                "description": "test",
                "max_iterations": 50,
                "phases": [],
            },
        )
        wf = Workflow.from_yaml(path)
        assert wf.max_iterations == 50

    def test_workflow_default_max_iterations(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "test.yaml",
            {"name": "wf", "phases": []},
        )
        wf = Workflow.from_yaml(path)
        assert wf.max_iterations == 100

    def test_total_steps_with_lanes(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    lanes=[
                        Lane(id="a", steps=[Step(task="A1"), Step(task="A2")]),
                        Lane(id="b", steps=[Step(task="B1")]),
                    ],
                ),
            ],
        )
        assert wf.total_steps() == 3

    def test_total_steps_mixed(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[Step(task="S1"), Step(task="S2")]),
                Phase(
                    name="P2",
                    lanes=[Lane(id="l1", steps=[Step(task="L1")])],
                ),
            ],
        )
        assert wf.total_steps() == 3


# ---------------------------------------------------------------------------
# 7.2 Template variable tests
# ---------------------------------------------------------------------------


class TestFillTemplateLanePhase:
    def test_lane_output_placeholder(self):
        ctx = {"lanes": {"correctness": {"output": "All good"}}}
        assert fill_template("{lane.correctness.output}", ctx) == "All good"

    def test_phase_output_placeholder(self):
        ctx = {"phases": {"scan": {"output": "Scanned"}}}
        assert fill_template("{phase.scan.output}", ctx) == "Scanned"

    def test_args_placeholder(self):
        ctx = {"args": {"topic": "AI"}}
        assert fill_template("Research {args.topic}", ctx) == "Research AI"

    def test_steps_dot_path(self):
        ctx = {"steps": {"web": {"output": "found it"}}}
        assert fill_template("{steps.web.output}", ctx) == "found it"

    def test_steps_exit_code(self):
        ctx = {"steps": {"web": {"output": "ok", "exit_code": 0}}}
        assert fill_template("{steps.web.exit_code}", ctx) == "0"

    def test_env_var(self):
        ctx = {"env": {"HOME": "/home/user"}}
        assert fill_template("{env.HOME}", ctx) == "/home/user"

    def test_previous(self):
        ctx = {"previous": "last output"}
        assert fill_template("{previous}", ctx) == "last output"

    def test_previous_none_kept(self):
        ctx = {"previous": None}
        assert fill_template("{previous}", ctx) == "{previous}"

    def test_unknown_placeholder_kept(self):
        assert fill_template("{unknown.thing}", {}) == "{unknown.thing}"

    def test_mixed(self):
        ctx = {
            "args": {"q": "test"},
            "steps": {"s1": {"output": "result"}},
            "previous": "prev",
        }
        result = fill_template("{args.q} + {steps.s1.output} + {previous}", ctx)
        assert result == "test + result + prev"


# ---------------------------------------------------------------------------
# Workflow from_yaml
# ---------------------------------------------------------------------------


class TestWorkflowFromYaml:
    def test_valid_yaml(self, tmp_path: Path):
        path = _write_yaml(
            tmp_path / "test.yaml",
            {
                "name": "my-wf",
                "description": "A test workflow",
                "phases": [
                    {
                        "name": "Step 1",
                        "steps": [{"task": "Hello"}],
                    }
                ],
            },
        )
        wf = Workflow.from_yaml(path)
        assert wf.name == "my-wf"
        assert wf.description == "A test workflow"
        assert len(wf.phases) == 1
        assert wf.phases[0].steps[0].task == "Hello"

    def test_missing_name_uses_stem(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "my-workflow.yaml", {"phases": []})
        wf = Workflow.from_yaml(path)
        assert wf.name == "my-workflow"

    def test_empty_phases(self, tmp_path: Path):
        path = _write_yaml(tmp_path / "empty.yaml", {"name": "empty"})
        wf = Workflow.from_yaml(path)
        assert wf.phases == []


class TestWorkflowState:
    def _make_wf(self) -> Workflow:
        return Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="Phase A", steps=[Step(task="A1"), Step(task="A2")]),
                Phase(name="Phase B", steps=[Step(task="B1")]),
            ],
        )

    def test_total_phases(self):
        assert self._make_wf().total_phases == 2

    def test_total_steps(self):
        assert self._make_wf().total_steps() == 3

    def test_progress_bar(self):
        wf = self._make_wf()
        wf.phase_index = 0
        assert wf.progress_bar() == "Phase 1/2"

    def test_progress_bar_no_phase(self):
        wf = self._make_wf()
        wf.phase_index = 5
        assert wf.progress_bar() == "Phase 6/2"

    def test_current_phase(self):
        wf = self._make_wf()
        wf.phase_index = 1
        assert wf.current_phase is not None
        assert wf.current_phase.name == "Phase B"

    def test_current_phase_out_of_range(self):
        wf = self._make_wf()
        wf.phase_index = 99
        assert wf.current_phase is None

    def test_current_step(self):
        wf = self._make_wf()
        wf.phase_index = 0
        wf.step_index = 0
        assert wf.current_step is not None
        assert wf.current_step.task == "A1"

    def test_summary(self):
        wf = self._make_wf()
        wf.results.append(
            StepResult(
                phase_index=0, step_index=0, task="A1",
                output="ok", exit_code=0, duration=1.5,
            )
        )
        s = wf.summary()
        assert "test" in s
        assert "[OK]" in s

    def test_detailed_summary_includes_output(self):
        wf = self._make_wf()
        wf.results.append(
            StepResult(
                phase_index=0, step_index=0, task="A1",
                output="Hello world", exit_code=0, duration=1.5,
            )
        )
        s = wf.detailed_summary()
        assert "Hello world" in s
        assert "[OK]" in s

    def test_detailed_summary_truncates_long_output(self):
        wf = self._make_wf()
        long_output = "\n".join(f"line {i}" for i in range(50))
        wf.results.append(
            StepResult(
                phase_index=0, step_index=0, task="A1",
                output=long_output, exit_code=0, duration=1.5,
            )
        )
        s = wf.detailed_summary()
        assert "more lines" in s

    def test_detailed_summary_shows_error(self):
        wf = self._make_wf()
        wf.results.append(
            StepResult(
                phase_index=0, step_index=0, task="A1",
                output="", exit_code=1, duration=0.5, error="boom",
            )
        )
        s = wf.detailed_summary()
        assert "[FAIL]" in s
        assert "boom" in s


class TestStepResult:
    def test_with_lane(self):
        """StepResult supports optional lane field."""
        sr = StepResult(
            phase_index=0, step_index=0, task="test",
            output="ok", exit_code=0, duration=1.0,
            lane="correctness",
        )
        assert sr.lane == "correctness"

    def test_lane_defaults_to_none(self):
        """StepResult.lane defaults to None for backward compat."""
        sr = StepResult(
            phase_index=0, step_index=0, task="test",
            output="ok", exit_code=0, duration=1.0,
        )
        assert sr.lane is None

    def test_without_lane_works(self):
        """Old-style StepResult without lane still works."""
        sr = StepResult(
            phase_index=0, step_index=0, task="test",
            output="ok", exit_code=0, duration=1.0,
        )
        assert sr.lane is None
        assert sr.exit_code == 0
        assert sr.output == "ok"


class TestRunnerStepDone:
    """Verify _step_done is called and passes correct info."""

    @pytest.mark.asyncio
    async def test_step_done_called_sequential(self):
        """_step_done is called after each step in sequential mode."""
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(id="s1", task="A"),
                    Step(id="s2", task="B"),
                ]),
            ],
        )
        calls = []
        runner = WorkflowRunner(wf, on_step_done=lambda *a: calls.append(a))
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a"),
                _make_subprocess_mock(b"b"),
            ]
            await runner.run()

        assert len(calls) == 2
        # Each call: (sr, phase_name, lane_name)
        sr0, pname0, lane0 = calls[0]
        assert isinstance(sr0, StepResult)
        assert pname0 == "P1"
        assert lane0 is None  # sequential
        assert sr0.output == "a"

        sr1, pname1, lane1 = calls[1]
        assert sr1.output == "b"

    @pytest.mark.asyncio
    async def test_step_done_called_lanes(self):
        """_step_done is called with lane info in parallel mode."""
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    lanes=[
                        Lane(id="a", name="Lane A", steps=[Step(id="a1", task="A1")]),
                        Lane(id="b", name="Lane B", steps=[Step(id="b1", task="B1")]),
                    ],
                ),
            ],
        )
        calls = []
        runner = WorkflowRunner(wf, on_step_done=lambda *a: calls.append(a))
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a-output"),
                _make_subprocess_mock(b"b-output"),
            ]
            await runner.run()

        assert len(calls) == 2
        lane_calls = {(c[2], c[0].output) for c in calls}
        assert ("Lane A", "a-output") in lane_calls
        assert ("Lane B", "b-output") in lane_calls

        # Verify sr.lane is set
        for sr, _, lane_name in calls:
            assert sr.lane is not None  # lane id, not name
            assert sr.lane in ("a", "b")

    @pytest.mark.asyncio
    async def test_step_done_with_lane_display(self):
        """Display.workflow_step_done receives lane info correctly."""
        from mocode.app.cli.display import Display

        display = Display()
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    lanes=[
                        Lane(id="x", name="Checker", steps=[Step(task="Check")]),
                    ],
                ),
            ],
        )
        calls = []
        original = display.workflow_step_done
        display.workflow_step_done = lambda *a: calls.append(a)

        runner = WorkflowRunner(wf, on_step_done=display.workflow_step_done)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"ok")
            await runner.run()

        assert len(calls) == 1
        sr, phase_name, lane_name = calls[0]
        assert phase_name == "P1"
        assert lane_name == "Checker"
        assert sr.lane == "x"


# ---------------------------------------------------------------------------
# WorkflowRegistry
# ---------------------------------------------------------------------------


class TestWorkflowRegistry:
    def test_discovers_yaml(self, tmp_path: Path):
        _write_yaml(
            tmp_path / "wf1.yaml",
            {"name": "wf1", "phases": [{"name": "p1", "steps": [{"task": "t"}]}]},
        )
        reg = WorkflowRegistry([tmp_path])
        assert reg.names() == ["wf1"]

    def test_discovers_yml_too(self, tmp_path: Path):
        _write_yaml(
            tmp_path / "wf2.yml",
            {"name": "wf2", "phases": []},
        )
        reg = WorkflowRegistry([tmp_path])
        assert "wf2" in reg.names()

    def test_skips_non_yaml(self, tmp_path: Path):
        (tmp_path / "readme.txt").write_text("not yaml", encoding="utf-8")
        reg = WorkflowRegistry([tmp_path])
        assert reg.names() == []

    def test_get(self, tmp_path: Path):
        _write_yaml(tmp_path / "wf.yaml", {"name": "my-wf", "phases": []})
        reg = WorkflowRegistry([tmp_path])
        assert reg.get("my-wf") is not None
        assert reg.get("nope") is None

    def test_list(self, tmp_path: Path):
        _write_yaml(tmp_path / "a.yaml", {"name": "a", "phases": []})
        _write_yaml(tmp_path / "b.yaml", {"name": "b", "phases": []})
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
        _write_yaml(tmp_path / "good.yaml", {"name": "good", "phases": []})
        reg = WorkflowRegistry([tmp_path])
        assert reg.names() == ["good"]

    def test_multiple_dirs(self, tmp_path: Path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        _write_yaml(d1 / "w1.yaml", {"name": "w1", "phases": []})
        _write_yaml(d2 / "w2.yaml", {"name": "w2", "phases": []})
        reg = WorkflowRegistry([d1, d2])
        assert set(reg.names()) == {"w1", "w2"}

    def test_later_dir_overwrites_same_name(self, tmp_path: Path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        _write_yaml(d1 / "shared.yaml", {"name": "shared", "description": "v1", "phases": []})
        _write_yaml(d2 / "shared.yaml", {"name": "shared", "description": "v2", "phases": []})
        reg = WorkflowRegistry([d1, d2])
        assert reg.get("shared").description == "v2"


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def _make_subprocess_mock(stdout: bytes = b"output", returncode: int = 0):
    """Create a mock for asyncio.create_subprocess_exec return value."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


# ---------------------------------------------------------------------------
# 7.3 Runner — Sequential steps
# ---------------------------------------------------------------------------


class TestRunnerSequential:
    @pytest.mark.asyncio
    async def test_steps_sequential(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(id="s1", task="Step 1"),
                    Step(id="s2", task="Result: {steps.s1.output}"),
                ]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"hello"),
                _make_subprocess_mock(b"world"),
            ]
            results = await runner.run()

        assert len(results) == 2
        assert results[0].output == "hello"
        assert results[1].task == "Result: hello"
        assert results[1].output == "world"
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_steps_goto_next(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(id="s1", task="First", goto=[GotoRule(to="next")]),
                    Step(id="s2", task="Second"),
                ]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"first"),
                _make_subprocess_mock(b"second"),
            ]
            results = await runner.run()

        assert len(results) == 2
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_steps_goto_end(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(id="s1", task="First", goto=[GotoRule(to="end")]),
                    Step(id="s2", task="Second"),
                ]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"done")
            results = await runner.run()

        assert len(results) == 1  # second step never ran
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_steps_goto_step_id(self):
        """Jump to a specific step within same phase."""
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(id="start", task="Start", goto=[
                        GotoRule(match="retry", to="retry_step"),
                    ]),
                    Step(id="middle", task="Middle"),
                    Step(id="retry_step", task="Retry", goto=[GotoRule(to="next")]),
                    Step(id="end", task="End"),
                ]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            # start outputs "retry" → triggers jump to retry_step
            mock_exec.side_effect = [
                _make_subprocess_mock(b"retry"),
                _make_subprocess_mock(b"middle"),
                _make_subprocess_mock(b"retry done"),
                _make_subprocess_mock(b"final"),
            ]
            results = await runner.run()

        # start → retry_step → end (middle is never visited)
        assert len(results) == 3
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_steps_goto_phase_cross_phase(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    id="phase_a",
                    name="A",
                    steps=[
                        Step(id="s1", task="In A", goto=[
                            GotoRule(to="phase.phase_c"),
                        ]),
                    ],
                ),
                Phase(id="phase_b", name="B", steps=[Step(task="In B")]),
                Phase(id="phase_c", name="C", steps=[Step(task="In C")]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"jump"),
                _make_subprocess_mock(b"done"),
            ]
            results = await runner.run()

        assert len(results) == 2  # A.s1 → C.s1 (phase_b skipped)
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_steps_goto_end_workflow(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(id="s1", task="First", goto=[
                        GotoRule(to="__end__"),
                    ]),
                ]),
                Phase(name="P2", steps=[Step(task="Second")]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"end all")
            results = await runner.run()

        assert len(results) == 1
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_steps_template_filled(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(id="s1", task="Hello {args.name}"),
                ]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"hi")
            results = await runner.run(args={"name": "World"})

        assert results[0].task == "Hello World"
        assert wf.status == "done"


# ---------------------------------------------------------------------------
# 7.4 Runner — Lanes (parallel)
# ---------------------------------------------------------------------------


class TestRunnerLanes:
    @pytest.mark.asyncio
    async def test_lanes_execute_all(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    lanes=[
                        Lane(id="a", steps=[Step(id="a1", task="Task A")]),
                        Lane(id="b", steps=[Step(id="b1", task="Task B")]),
                    ],
                    goto=[GotoRule(to="next")],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"result-a"),
                _make_subprocess_mock(b"result-b"),
            ]
            results = await runner.run()

        assert len(results) == 2
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_lanes_output_in_context(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    lanes=[
                        Lane(id="correctness", steps=[Step(task="Check")]),
                    ],
                    goto=[GotoRule(to="next")],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"all good")
            await runner.run()

        assert runner._context["lanes"]["correctness"]["output"] == "all good"

    @pytest.mark.asyncio
    async def test_lanes_phase_goto_after_all_done(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    lanes=[
                        Lane(id="a", steps=[Step(task="A")]),
                        Lane(id="b", steps=[Step(task="B")]),
                    ],
                    goto=[GotoRule(to="phase.p2")],
                ),
                Phase(id="p2", name="P2", steps=[Step(task="Final")]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a"),
                _make_subprocess_mock(b"b"),
                _make_subprocess_mock(b"final"),
            ]
            results = await runner.run()

        assert len(results) == 3
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_lanes_multi_step_lane(self):
        """Each lane can have multiple sequential steps."""
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    lanes=[
                        Lane(id="a", steps=[
                            Step(id="a1", task="First A"),
                            Step(id="a2", task="Second A"),
                        ]),
                        Lane(id="b", steps=[Step(id="b1", task="Only B")]),
                    ],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a1"),
                _make_subprocess_mock(b"b1"),
                _make_subprocess_mock(b"a2"),
            ]
            results = await runner.run()

        assert len(results) == 3
        assert wf.status == "done"


# ---------------------------------------------------------------------------
# 7.5 Runner — Goto resolution
# ---------------------------------------------------------------------------


class TestGotoResolution:
    def test_resolve_goto_match(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        rules = [GotoRule(match="error.*", to="phase.retry")]
        assert runner._resolve_goto(rules, "critical error occurred", "step_p1_s1") == "phase.retry"

    def test_resolve_goto_default(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        rules = [GotoRule(to="end")]
        assert runner._resolve_goto(rules, "anything", "step_p1_s1") == "end"

    def test_resolve_goto_first_match_wins(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        rules = [
            GotoRule(match="error", to="phase.retry"),
            GotoRule(to="next"),
        ]
        assert runner._resolve_goto(rules, "all good", "step_p1_s1") == "next"
        assert runner._resolve_goto(rules, "found an error", "step_p1_s1") == "phase.retry"

    def test_resolve_goto_no_match_returns_next(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        rules = [GotoRule(match="specific", to="end")]
        assert runner._resolve_goto(rules, "something else", "step_p1_s1") == "next"

    def test_resolve_goto_max_limit(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        rules = [GotoRule(match="error", to="phase.retry", max=2)]

        # First two hits go to retry
        assert runner._resolve_goto(rules, "error", "step_p1_s1") == "phase.retry"
        assert runner._resolve_goto(rules, "error", "step_p1_s1") == "phase.retry"
        # Third hit exceeds max → falls through to next
        assert runner._resolve_goto(rules, "error", "step_p1_s1") == "next"

    def test_resolve_goto_max_zero_unlimited(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        rules = [GotoRule(match="error", to="phase.retry", max=0)]

        for _ in range(10):
            assert runner._resolve_goto(rules, "error", "step_p1_s1") == "phase.retry"

    def test_resolve_goto_empty_rules(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        assert runner._resolve_goto([], "anything", "step_p1_s1") == "next"

    def test_resolve_goto_multiple_rules_with_max(self):
        runner = WorkflowRunner(Workflow(name="test", description="", phases=[]))
        rules = [
            GotoRule(match="error", to="phase.retry", max=1),
            GotoRule(to="end"),
        ]
        assert runner._resolve_goto(rules, "error", "step_p1_s1") == "phase.retry"
        # max hit → falls through to default
        assert runner._resolve_goto(rules, "error again", "step_p1_s1") == "end"


# ---------------------------------------------------------------------------
# 7.6 Runner — Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_workflow_max_iterations_loop_limit(self):
        """Global phase entry limit triggers loop_limit status."""
        wf = Workflow(
            name="test",
            description="",
            max_iterations=3,
            phases=[
                Phase(
                    id="loop",
                    name="Loop",
                    steps=[Step(id="s1", task="Do it", goto=[
                        GotoRule(to="phase.loop"),
                    ])],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"again")
            await runner.run()

        # 3 entries max → enters 3rd time, hits limit, stops
        assert wf.status == "loop_limit"

    @pytest.mark.asyncio
    async def test_phase_max_iterations_loop_limit(self):
        """Phase-level max_iterations overrides lower than workflow default."""
        wf = Workflow(
            name="test",
            description="",
            max_iterations=100,
            phases=[
                Phase(
                    id="loop",
                    name="Loop",
                    max_iterations=2,
                    steps=[Step(id="s1", task="Do it", goto=[
                        GotoRule(to="phase.loop"),
                    ])],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"again")
            await runner.run()

        assert wf.status == "loop_limit"


# ---------------------------------------------------------------------------
# Runner — Timeout / error handling
# ---------------------------------------------------------------------------


class TestRunnerTimeout:
    @pytest.mark.asyncio
    async def test_timeout(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[Step(task="Slow")]),
            ],
        )
        runner = WorkflowRunner(wf, timeout=1)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            proc = MagicMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_exec.return_value = proc
            results = await runner.run()

        assert results[0].exit_code == 1
        assert "timed out" in results[0].error
        # In the new design, step errors are handled via goto rules;
        # the workflow completes normally unless a circuit breaker trips.
        assert wf.status == "done"


class TestRunnerProgress:
    @pytest.mark.asyncio
    async def test_progress_callback_called(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[Step(task="Step A"), Step(task="Step B")]),
            ],
        )
        progress_calls = []
        runner = WorkflowRunner(wf, on_progress=progress_calls.append)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"out-a"),
                _make_subprocess_mock(b"out-b"),
            ]
            await runner.run()

        assert any("Step 1" in msg for msg in progress_calls)
        assert any("Step 2" in msg for msg in progress_calls)

    @pytest.mark.asyncio
    async def test_progress_callback_none_is_safe(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[Step(task="A")]),
            ],
        )
        runner = WorkflowRunner(wf)  # no on_progress
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"ok")
            results = await runner.run()
        assert results[0].exit_code == 0


# ---------------------------------------------------------------------------
# 7.7 CLI command tests
# ---------------------------------------------------------------------------


class TestWorkflowCommand:
    @pytest.fixture
    def _setup_registry(self, tmp_path: Path):
        _write_yaml(
            tmp_path / "demo.yaml",
            {
                "name": "demo",
                "description": "Demo workflow",
                "phases": [
                    {"name": "P1", "steps": [{"task": "Hello"}]},
                ],
            },
        )
        return tmp_path

    @pytest.mark.asyncio
    async def test_list_subcommand(self, _setup_registry, tmp_path: Path):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        reg = WorkflowRegistry([tmp_path])
        app = MagicMock()
        app.workflow_registry = reg
        display = MagicMock()

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="list"))
        assert result.kind == "continue"
        display.info.assert_called_once()
        assert "demo" in display.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_subcommand(self, _setup_registry, tmp_path: Path):
        from mocode.app.cli.commands.workflow import WorkflowCommand

        reg = WorkflowRegistry([tmp_path])
        app = MagicMock()
        app.workflow_registry = reg
        display = MagicMock()

        cmd = WorkflowCommand()
        result = await cmd.run(_make_ctx(app=app, display=display, args="show demo"))
        assert result.kind == "continue"
        display.info.assert_called_once()
        assert "P1" in display.info.call_args[0][0]

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

"""Tests for workflow engine — models, fill_template, registry, runner, command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.app.workflow import (
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
# Step / Phase models
# ---------------------------------------------------------------------------


class TestStep:
    def test_from_dict_basic(self):
        step = Step.from_dict({"id": "web", "task": "Search the web"})
        assert step.id == "web"
        assert step.task == "Search the web"

    def test_from_dict_defaults(self):
        step = Step.from_dict({})
        assert step.id == ""
        assert step.task == ""


class TestPhase:
    def test_from_dict_basic(self):
        phase = Phase.from_dict({
            "id": "research",
            "name": "Research",
            "steps": [{"id": "web", "task": "Search"}],
        })
        assert phase.id == "research"
        assert phase.name == "Research"
        assert len(phase.steps) == 1
        assert phase.steps[0].id == "web"

    def test_from_dict_defaults(self):
        phase = Phase.from_dict({"name": "Test"})
        assert phase.id == ""
        assert phase.parallel is False
        assert phase.max_attempts == 1
        assert phase.halt_if is None
        assert phase.steps == []

    def test_from_dict_loop_config(self):
        phase = Phase.from_dict({
            "name": "Retry",
            "max_attempts": 3,
            "halt_if": "PASS",
            "steps": [{"task": "Do it"}],
        })
        assert phase.max_attempts == 3
        assert phase.halt_if == "PASS"


# ---------------------------------------------------------------------------
# fill_template
# ---------------------------------------------------------------------------


class TestFillTemplate:
    def test_args_placeholder(self):
        ctx = {"args": {"topic": "AI"}}
        assert fill_template("Research {args.topic}", ctx) == "Research AI"

    def test_steps_dot_path(self):
        ctx = {"steps": {"web": {"output": "found it", "exit_code": 0}}}
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
# Workflow
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
        wf.step_index = 1
        assert wf.progress_bar() == "Phase 1/2 · Step 2/2"

    def test_progress_bar_no_step(self):
        wf = self._make_wf()
        wf.phase_index = 5  # out of range
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
# WorkflowRunner
# ---------------------------------------------------------------------------


def _make_subprocess_mock(stdout: bytes = b"output", returncode: int = 0):
    """Create a mock for asyncio.create_subprocess_exec return value."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


class TestWorkflowRunnerSerial:
    @pytest.mark.asyncio
    async def test_serial_execution(self, tmp_path: Path):
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
        assert results[1].task == "Result: hello"  # template filled
        assert results[1].output == "world"
        assert wf.status == "done"

    @pytest.mark.asyncio
    async def test_serial_stops_on_failure(self, tmp_path: Path):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(task="OK step"),
                    Step(task="Fail step"),
                    Step(task="Should not run"),
                ]),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"ok", returncode=0),
                _make_subprocess_mock(b"fail", returncode=1),
            ]
            results = await runner.run()

        assert len(results) == 2  # third step never ran
        assert wf.status == "error"


class TestWorkflowRunnerParallel:
    @pytest.mark.asyncio
    async def test_parallel_execution(self, tmp_path: Path):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    parallel=True,
                    steps=[
                        Step(id="a", task="Task A"),
                        Step(id="b", task="Task B"),
                    ],
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
    async def test_parallel_one_fails(self, tmp_path: Path):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    parallel=True,
                    steps=[
                        Step(task="OK"),
                        Step(task="Fail"),
                    ],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"ok", returncode=0),
                _make_subprocess_mock(b"err", returncode=1),
            ]
            results = await runner.run()

        assert wf.status == "error"


class TestWorkflowRunnerLoop:
    @pytest.mark.asyncio
    async def test_retry_on_failure(self, tmp_path: Path):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    max_attempts=3,
                    steps=[Step(task="Try")],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"fail", returncode=1),
                _make_subprocess_mock(b"ok", returncode=0),
            ]
            results = await runner.run()

        assert wf.status == "done"
        assert mock_exec.call_count == 2

    @pytest.mark.asyncio
    async def test_halt_if_stops_early(self, tmp_path: Path):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    max_attempts=5,
                    halt_if=r"DONE",
                    steps=[Step(task="Check")],
                ),
            ],
        )
        runner = WorkflowRunner(wf)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_subprocess_mock(b"status: DONE")
            results = await runner.run()

        assert wf.status == "done"
        assert mock_exec.call_count == 1  # halted after first attempt


class TestWorkflowRunnerTimeout:
    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path: Path):
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
        assert wf.status == "error"


class TestWorkflowRunnerProgress:
    @pytest.mark.asyncio
    async def test_progress_callback_called_for_serial_steps(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(name="P1", steps=[
                    Step(task="Step A"),
                    Step(task="Step B"),
                ]),
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

        # phase start + 2×(step start + step done)
        assert any("Phase 1" in msg for msg in progress_calls)
        assert any("Step 1" in msg for msg in progress_calls)
        assert any("Step 2" in msg for msg in progress_calls)
        assert any("✓" in msg for msg in progress_calls)

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

    @pytest.mark.asyncio
    async def test_progress_parallel_reports_running_and_done(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    parallel=True,
                    steps=[Step(task="A"), Step(task="B")],
                ),
            ],
        )
        progress_calls = []
        runner = WorkflowRunner(wf, on_progress=progress_calls.append)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"a"),
                _make_subprocess_mock(b"b"),
            ]
            await runner.run()

        assert any("parallel" in msg.lower() for msg in progress_calls)
        assert any("2/2 ok" in msg for msg in progress_calls)

    @pytest.mark.asyncio
    async def test_progress_loop_reports_attempts(self):
        wf = Workflow(
            name="test",
            description="",
            phases=[
                Phase(
                    name="P1",
                    max_attempts=3,
                    steps=[Step(task="Try")],
                ),
            ],
        )
        progress_calls = []
        runner = WorkflowRunner(wf, on_progress=progress_calls.append)
        with patch("mocode.app.workflow.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = [
                _make_subprocess_mock(b"fail", returncode=1),
                _make_subprocess_mock(b"ok", returncode=0),
            ]
            await runner.run()

        assert any("Attempt 1" in msg for msg in progress_calls)
        assert any("Attempt 2" in msg for msg in progress_calls)


# ---------------------------------------------------------------------------
# WorkflowCommand
# ---------------------------------------------------------------------------


class TestWorkflowCommand:
    @pytest.fixture
    def _setup_registry(self, tmp_path: Path):
        """Create a temporary registry with one workflow."""
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

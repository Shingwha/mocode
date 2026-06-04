"""MoCode 0.3 — CLI entry point.

Usage:
    mocode                                              Launch interactive CLI
    mocode -p "prompt"                                  Non-interactive oneshot
    mocode workflow run <name> [key=value...]            Run workflow (background)
    mocode workflow run <name> --fg [key=value...]       Run workflow (foreground)
    mocode workflow list                                 List available workflows
    mocode workflow show <name>                          Show DAG structure
    mocode workflow status [run_id]                      Check run status
    mocode workflow result [run_id]                      Print full results
    mocode workflow stop <run_id>                        Kill background workflow
    mocode workflow runs                                 List recent runs
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

from .cli_args import parse_args, read_stdin_if_piped


def main():
    args = parse_args()

    if getattr(args, "command", None) == "workflow":
        _run_workflow_cli(args)
    elif args.prompt is not None:
        _run_app(interactive=False, prompt=args.prompt, stdin_text=read_stdin_if_piped())
    else:
        _run_app(interactive=True)


def _run_app(*, interactive: bool, prompt: str | None = None, stdin_text: str | None = None):
    """Unified entry: create CLIApp, check config, dispatch to run or run_oneshot."""
    from .app.cli import CLIApp

    app = CLIApp(interactive=interactive)
    if app.config is None:
        print("Config not found. Create ~/.mocode/config.json first.", file=sys.stderr)
        sys.exit(1)

    if interactive:
        app.run()
    else:
        app.run_oneshot(prompt, stdin_text)


# ── Workflow CLI dispatch ──────────────────────────────────


def _run_workflow_cli(args) -> None:
    """Top-level dispatch for ``mocode workflow <action>``."""
    action = getattr(args, "workflow_action", None)
    if not action:
        print("Usage: mocode workflow <run|list|show|status|stop|result|runs>", file=sys.stderr)
        sys.exit(1)

    dispatch = {
        "run": _workflow_run,
        "list": _workflow_list,
        "show": _workflow_show,
        "status": _workflow_status,
        "stop": _workflow_stop,
        "result": _workflow_result,
        "runs": _workflow_runs,
    }
    handler = dispatch.get(action)
    if handler is None:
        print(f"Unknown workflow action: {action}", file=sys.stderr)
        sys.exit(1)
    handler(args)


# ── Workflow actions ───────────────────────────────────────


def _workflow_run(args) -> None:
    if args.fg:
        _run_workflow_foreground(args)
    else:
        _run_workflow_background(args)


def _run_workflow_foreground(args) -> None:
    """Run workflow in foreground, persist results, print summary."""
    from .app.workflow import WorkflowRegistry, WorkflowRunStore, detailed_summarize
    from .app.workflow.runner import DAGRunner

    name = args.name
    user_args = _parse_kv_args(args.kv_args)

    registry = _make_registry()
    wf = registry.get(name)
    if wf is None:
        print(f"Workflow '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    store = WorkflowRunStore()
    run_id = args._run_id or store.create(
        workflow_name=name,
        workflow_path=str(wf.path),
        args=user_args,
    )

    print(f"Running workflow '{name}' (run_id: {run_id}) ...", file=sys.stderr)

    runner = DAGRunner(wf, run_id=run_id, run_store=store)
    try:
        results = asyncio.run(runner.run(args=user_args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Workflow failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(detailed_summarize(wf, results))

    # Exit code 1 if any node failed
    if any(r.exit_code != 0 for r in results):
        sys.exit(1)


def _run_workflow_background(args) -> None:
    """Spawn a detached child process, print run_id, exit immediately."""
    from .app.workflow import WorkflowRegistry, WorkflowRunStore

    name = args.name
    user_args = _parse_kv_args(args.kv_args)

    registry = _make_registry()
    wf = registry.get(name)
    if wf is None:
        print(f"Workflow '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    store = WorkflowRunStore()
    run_id = store.create(
        workflow_name=name,
        workflow_path=str(wf.path),
        args=user_args,
    )

    # Build child command: mocode workflow run <name> --fg --_run-id <id> [kv_args...]
    cmd = [_find_mocode_bin(), "workflow", "run", name, "--fg", "--_run-id", run_id]
    cmd.extend(args.kv_args)

    log_path = store._base_dir / f"{run_id}.log"

    # Spawn detached child
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True

    with open(log_path, "w", encoding="utf-8") as log_f:
        proc = subprocess.Popen(
            cmd,
            stdout=log_f,
            stderr=log_f,
            **kwargs,
        )

    store.update_pid(run_id, proc.pid)
    print(run_id)


def _workflow_list(args) -> None:
    """List available workflows."""
    from .app.cli.display import Display
    from .app.workflow import WorkflowRegistry

    registry = _make_registry()
    workflows = registry.list()
    if not workflows:
        print("No workflows found.")
        return

    display = Display()
    print(display.workflow_list(workflows))


def _workflow_show(args) -> None:
    """Show a workflow's DAG structure."""
    from .app.cli.display import Display
    from .app.workflow import WorkflowRegistry

    registry = _make_registry()
    wf = registry.get(args.name)
    if wf is None:
        print(f"Workflow '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    display = Display()
    print(display.workflow_show(wf))


def _workflow_status(args) -> None:
    """Check run status, with PID liveness check."""
    from .app.workflow import WorkflowRunStore

    store = WorkflowRunStore()
    run_id = store.resolve_run_id(args.run_id)
    if run_id is None:
        print("No workflow runs found.", file=sys.stderr)
        sys.exit(1)

    record = store.load(run_id)
    if record is None:
        print(f"Run '{run_id}' not found.", file=sys.stderr)
        sys.exit(1)

    status = record.get("status", "unknown")

    # Liveness check: if JSON says running but PID is dead → crashed
    if status == "running":
        pid = record.get("pid")
        if pid and not store.is_alive(run_id):
            status = "crashed"

    print(f"Run:       {run_id}")
    print(f"Workflow:  {record.get('workflow_name', '?')}")
    print(f"Status:    {status}")
    print(f"Started:   {record.get('started_at', '?')}")

    if record.get("finished_at"):
        print(f"Finished:  {record['finished_at']}")
    if record.get("wall_duration") is not None:
        print(f"Duration:  {record['wall_duration']:.1f}s")
    if record.get("pid"):
        alive = "alive" if store.is_alive(run_id) else "dead"
        print(f"PID:       {record['pid']} ({alive})")

    results = record.get("results", [])
    if results:
        done = sum(1 for r in results if r.get("status") == "done" and r.get("exit_code") == 0)
        failed = sum(1 for r in results if r.get("exit_code", 0) != 0)
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        print(f"Nodes:     {done} done, {failed} failed, {skipped} skipped")

    if status == "crashed":
        log_path = store._base_dir / f"{run_id}.log"
        print(f"\nProcess died unexpectedly. Check log: {log_path}", file=sys.stderr)


def _workflow_stop(args) -> None:
    """Kill a running background workflow."""
    from .app.workflow import WorkflowRunStore
    from datetime import datetime

    store = WorkflowRunStore()
    record = store.load(args.run_id)
    if record is None:
        print(f"Run '{args.run_id}' not found.", file=sys.stderr)
        sys.exit(1)

    status = record.get("status")
    if status != "running":
        print(f"Run '{args.run_id}' is not running (status: {status}).")
        return

    pid = record.get("pid")
    if pid is None:
        print(f"Run '{args.run_id}' has no PID recorded.", file=sys.stderr)
        sys.exit(1)

    if not store.is_alive(args.run_id):
        # Mark as crashed since process already dead
        from .app.workflow.models import NodeResult
        results = [NodeResult(**r) for r in record.get("results", [])]
        store.update(args.run_id, results, "crashed", datetime.now().isoformat())
        print(f"Process for '{args.run_id}' already dead. Marked as crashed.")
        return

    # Kill the process
    try:
        if sys.platform == "win32":
            # taskkill /T kills the process tree, /F forces
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            # If still alive, SIGKILL
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception as e:
        print(f"Failed to kill process {pid}: {e}", file=sys.stderr)
        sys.exit(1)

    from .app.workflow.models import NodeResult
    results = [NodeResult(**r) for r in record.get("results", [])]
    store.update(args.run_id, results, "cancelled", datetime.now().isoformat())
    print(f"Run '{args.run_id}' cancelled.")


def _workflow_result(args) -> None:
    """Print full results for a run."""
    from .app.workflow import WorkflowRunStore, detailed_summarize
    from .app.workflow.models import NodeResult, Workflow

    store = WorkflowRunStore()
    run_id = store.resolve_run_id(args.run_id)
    if run_id is None:
        print("No workflow runs found.", file=sys.stderr)
        sys.exit(1)

    record = store.load(run_id)
    if record is None:
        print(f"Run '{run_id}' not found.", file=sys.stderr)
        sys.exit(1)

    # Raw JSON mode
    if getattr(args, "json_output", False):
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return

    # Text mode: reconstruct Workflow and NodeResults for detailed_summarize
    results = [NodeResult(**r) for r in record.get("results", [])]
    wf = Workflow(
        name=record.get("workflow_name", "?"),
        description="",
        nodes=[],
        path=record.get("workflow_path"),
    )

    print(f"Run:       {run_id}")
    print(f"Status:    {record.get('status', '?')}")
    if record.get("wall_duration") is not None:
        print(f"Duration:  {record['wall_duration']:.1f}s")
    print()

    if results:
        print(detailed_summarize(wf, results, max_lines=9999))
    else:
        print("No node results yet.")


def _workflow_runs(args) -> None:
    """List recent runs."""
    from .app.workflow import WorkflowRunStore

    store = WorkflowRunStore()
    runs = store.list_recent()

    if not runs:
        print("No workflow runs found.")
        return

    for r in runs:
        rid = r.get("run_id", "?")
        name = r.get("workflow_name", "?")
        status = r.get("status", "?")
        started = r.get("started_at", "?")[:19]  # trim microseconds

        alive_tag = ""
        if status == "running" and r.get("pid"):
            alive_tag = " (alive)" if store.is_alive(rid) else " (dead)"

        print(f"  {rid}  {name:20s}  {status}{alive_tag}  {started}")


# ── Helpers ────────────────────────────────────────────────


def _find_mocode_bin() -> str:
    """Locate the ``mocode`` executable for subprocess spawning."""
    import shutil

    mocode = shutil.which("mocode")
    if mocode:
        return mocode
    # Fallback: same interpreter + -m mocode (requires __main__.py)
    return f"{sys.executable} -m mocode"


def _make_registry():
    """Build a WorkflowRegistry from config dirs."""
    from pathlib import Path

    from .app.workflow import WorkflowRegistry

    dirs: list[Path] = []
    # Global workflows dir
    global_dir = Path.home() / ".mocode" / "workflows"
    dirs.append(global_dir)
    # Project-local workflows dir
    local_dir = Path.cwd() / ".mocode" / "workflows"
    if local_dir != global_dir:
        dirs.append(local_dir)

    return WorkflowRegistry(dirs=dirs)


def _parse_kv_args(kv_args: list[str] | None) -> dict[str, str]:
    """Parse ``key=value`` pairs from positional args."""
    result: dict[str, str] = {}
    for p in kv_args or []:
        if "=" in p:
            k, v = p.split("=", 1)
            result[k.strip()] = v.strip()
    return result


if __name__ == "__main__":
    main()

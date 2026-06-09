"""/workflow command — manage and run YAML workflow DAGs."""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime

from ...workflow.models import parse_args
from ...workflow.runner import DAGRunner
from ...workflow.run_store import WorkflowRunStore
from ..prompts import Choice, select
from ..spinner import Priority, Truncate
from . import Command, CommandContext, CommandResult, Subcommand


# ── Standalone handler functions ────────────────────────────


async def _list(ctx: CommandContext, args_str: str) -> CommandResult:
    registry = ctx.app.workflow_registry
    workflows = registry.list()
    if not workflows:
        ctx.display.info("No workflows found.")
        return CommandResult.CONTINUE

    text = ctx.app.wf_renderer.list_workflows(workflows)
    ctx.display.info("Workflows:\n" + text)
    return CommandResult.CONTINUE


async def _show(ctx: CommandContext, args_str: str) -> CommandResult:
    name = args_str.strip()
    wf = _find_workflow(ctx, name)
    if wf is None:
        return CommandResult.CONTINUE

    text = ctx.app.wf_renderer.show(wf)
    ctx.display.info(text)
    return CommandResult.CONTINUE


async def _run(ctx: CommandContext, args_str: str) -> CommandResult:
    parts = args_str.split()
    if not parts:
        ctx.display.warn("Usage: /workflow run <name> [positional...] [key=value...]")
        return CommandResult.CONTINUE

    name = parts[0]
    wf = _find_workflow(ctx, name)
    if wf is None:
        return CommandResult.CONTINUE

    try:
        user_args = parse_args(wf.params, parts[1:])
    except ValueError as e:
        ctx.display.warn(str(e))
        return CommandResult.CONTINUE

    # Persist run
    store = WorkflowRunStore()
    run_id = store.create(
        workflow_name=name,
        workflow_path=str(wf.path),
        args=user_args,
    )

    ctx.app.wf_renderer.start(wf)

    runner = DAGRunner(
        wf,
        parent_agent=ctx.app.agent,
        on_event=ctx.app.wf_renderer.handle_event,
        run_id=run_id,
        run_store=store,
        timeout=wf.timeout,
    )
    try:
        task = asyncio.ensure_future(runner.run(args=user_args))

        def _on_sigint(signum, frame):
            if not task.done():
                task.cancel()

        original_handler = signal.signal(signal.SIGINT, _on_sigint)
        try:
            async with ctx.display.spinner():
                ctx.display.spinner_set("wf_tag", wf.name,
                                        priority=Priority.NORMAL, truncate=Truncate.TAIL)
                results = await task
        finally:
            signal.signal(signal.SIGINT, original_handler)

    except asyncio.CancelledError:
        ctx.display.print()  # newline after spinner
        ctx.app.wf_renderer.cancelled(wf, runner.partial_results)
        store.update(run_id, runner.partial_results, "cancelled",
                     datetime.now().isoformat())
        return CommandResult.CONTINUE
    except Exception as e:
        ctx.display.error(f"Workflow failed: {e}")
        return CommandResult.CONTINUE

    ctx.app.wf_renderer.summary(wf, results)
    ctx.display.info(f"Run ID: {run_id}")
    ctx.display.info(f"Run dir: {store.run_dir(run_id)}")
    return CommandResult.CONTINUE


async def _status(ctx: CommandContext, args_str: str) -> CommandResult:
    store = WorkflowRunStore()
    raw = args_str.strip()

    # No arguments: show recent run list
    if not raw:
        runs = store.list_recent()
        if not runs:
            ctx.display.warn("No workflow runs found.")
            return CommandResult.CONTINUE
        text = ctx.app.wf_renderer.list_runs(runs, store)
        ctx.display.info(text)
        return CommandResult.CONTINUE

    # With arguments: show specific run detail
    record = _resolve_run(store, raw)
    if record is None:
        ctx.display.warn(f"Run '{raw}' not found.")
        return CommandResult.CONTINUE

    run_id = record["run_id"]
    status = record.get("status", "unknown")

    # Liveness check: if JSON says running but PID is dead → crashed
    if status == "running" and record.get("pid") and not store.is_alive(run_id):
        status = "crashed"

    text = ctx.app.wf_renderer.run_detail(record, status)
    ctx.display.info(text)
    return CommandResult.CONTINUE


async def _result(ctx: CommandContext, args_str: str) -> CommandResult:
    """Show output files for a run."""
    store = WorkflowRunStore()
    raw = args_str.strip()
    record = _resolve_run(store, raw)
    if record is None:
        ctx.display.warn(f"Run '{raw}' not found.")
        return CommandResult.CONTINUE

    run_id = record["run_id"]
    rd = store.run_dir(run_id)

    if not rd.exists():
        ctx.display.warn(f"Run directory not found: {rd}")
        return CommandResult.CONTINUE

    # Collect files in run directory
    files: list[str] = []
    for p in sorted(rd.rglob("*")):
        if p.is_file():
            rel = p.relative_to(rd)
            size = p.stat().st_size
            files.append(f"  {rel}  ({size} bytes)")

    if not files:
        ctx.display.info(f"Run {run_id} — no output files yet.")
    else:
        header = f"Run {run_id} — output files:"
        ctx.display.info(header + "\n" + "\n".join(files))
    return CommandResult.CONTINUE


# ── Interactive menu ──────────────────────────────────────


async def _interactive_menu(ctx: CommandContext, group: Command) -> CommandResult:
    registry = ctx.app.workflow_registry
    workflows = registry.list()
    if not workflows:
        ctx.display.info("No workflows found.")
        return CommandResult.CONTINUE

    choices = []
    for wf in workflows:
        desc = wf.description[:50] if wf.description else ""
        choices.append(Choice(
            title=f"Run  {wf.name}",
            value=f"run:{wf.name}",
            description=desc,
        ))
        choices.append(Choice(
            title=f"Show {wf.name}",
            value=f"show:{wf.name}",
            description="View DAG structure",
        ))

    choices.append(Choice(title="Recent runs", value="__runs__"))
    choices.append(Choice(title="Back", value="__back__"))

    chosen = await select("Workflows:", choices)
    if chosen is None or chosen == "__back__":
        return CommandResult.CONTINUE
    if chosen == "__runs__":
        return await _status(ctx, "")

    if chosen.startswith("run:"):
        wf_name = chosen[4:]
        ctx.display.set_pending_input(f"/workflow run {wf_name}")
    elif chosen.startswith("show:"):
        wf_name = chosen[5:]
        return await _show(ctx, wf_name)

    return CommandResult.CONTINUE


# ── Module-level helpers ──────────────────────────────────


def _find_workflow(ctx: CommandContext, name: str) -> object | None:
    """Look up a workflow by name; display a warning if not found."""
    registry = ctx.app.workflow_registry
    wf = registry.get(name)
    if wf is None:
        ctx.display.warn(f"Workflow '{name}' not found.")
    return wf


def _resolve_run(store: WorkflowRunStore, raw: str) -> dict | None:
    """Resolve run_id or workflow name to a run record."""
    if raw.startswith("wf_"):
        return store.load(raw)
    if raw:
        record = store.find_latest(workflow_name=raw)
        if record:
            return record
        return store.load(store.resolve_run_id(raw))
    return store.find_latest()


# ── Command definition ────────────────────────────────────


command = Command(
    name="/workflow",
    description="Manage and run YAML workflows",
    aliases=("workflow",),
    subcommands=(
        Subcommand(("list", "ls"), "List available workflows", _list),
        Subcommand("show", "Show workflow details", _show),
        Subcommand("run", "Run a workflow", _run),
        Subcommand("status", "Check run status or view recent runs", _status),
        Subcommand("result", "Show run output files", _result),
    ),
    menu=_interactive_menu,
)

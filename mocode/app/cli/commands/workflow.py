"""/workflow command — manage and run YAML workflow DAGs."""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime
from pathlib import Path

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

    # Persist run — store location derived from workflow YAML path
    store = WorkflowRunStore.from_workflow_path(wf.path)
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
    raw = args_str.strip()

    # No arguments: show recent run list from all stores
    if not raw:
        all_runs: list[dict] = []
        for store in _list_all_stores():
            all_runs.extend(store.list_recent(limit=20))
        all_runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        all_runs = all_runs[:20]
        if not all_runs:
            ctx.display.warn("No workflow runs found.")
            return CommandResult.CONTINUE
        # Use default store for rendering (renderer only needs run_dir path)
        text = ctx.app.wf_renderer.list_runs(all_runs, WorkflowRunStore())
        ctx.display.info(text)
        return CommandResult.CONTINUE

    # With arguments: show specific run detail (search all stores)
    result = _find_store_for_run(raw)
    if result is None:
        ctx.display.warn(f"Run '{raw}' not found.")
        return CommandResult.CONTINUE
    store, record = result

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
    raw = args_str.strip()
    result = _find_store_for_run(raw)
    if result is None:
        ctx.display.warn(f"Run '{raw}' not found.")
        return CommandResult.CONTINUE
    store, record = result

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


def _find_store_for_run(raw: str) -> tuple[WorkflowRunStore, dict | None] | None:
    """Find the store and record for a run identifier.

    Searches global store first, then local stores if not found.
    Returns (store, record) or None.
    """
    global_store = WorkflowRunStore()
    record = _resolve_run(global_store, raw)
    if record is not None:
        return (global_store, record)

    # Derive local store from the workflow_path in recent global runs.
    # Check recent global runs' workflow paths for local runs.
    for g_rec in global_store.list_recent(limit=20):
        wf_path = g_rec.get("workflow_path")
        if wf_path:
            local_store = WorkflowRunStore.from_workflow_path(Path(wf_path))
            if local_store._base_dir != global_store._base_dir and local_store._base_dir.exists():
                record = _resolve_run(local_store, raw)
                if record is not None:
                    return (local_store, record)

    return None


def _list_all_stores() -> list[WorkflowRunStore]:
    """Return all known stores: global + any local runs/ directories."""
    global_store = WorkflowRunStore()
    stores = [global_store]
    # Discover local stores by scanning for .mocode directories with workflows/
    for md_dir in Path.cwd().parents:
        local_wfs = md_dir / ".mocode" / "workflows"
        local_runs = md_dir / ".mocode" / "runs"
        if local_wfs.is_dir() and local_runs.is_dir():
            store = WorkflowRunStore(base_dir=local_runs)
            if store._base_dir != global_store._base_dir:
                stores.append(store)
            break  # nearest .mocode wins
    # Also check home dir local stores
    home_runs = Path.home() / ".mocode" / "runs"
    if home_runs.is_dir():
        home_store = WorkflowRunStore(base_dir=home_runs)
        if home_store._base_dir != global_store._base_dir:
            stores.append(home_store)
    return stores


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

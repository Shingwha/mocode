"""/workflow command — manage and run YAML workflow DAGs."""

from __future__ import annotations

import asyncio
import json

from ...workflow.models import NodeResult, parse_args
from ..workflow_renderer import detailed_summarize
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
    registry = ctx.app.workflow_registry
    wf = registry.get(name)
    if wf is None:
        ctx.display.warn(f"Workflow '{name}' not found.")
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

    registry = ctx.app.workflow_registry
    wf = registry.get(name)
    if wf is None:
        ctx.display.warn(f"Workflow '{name}' not found.")
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
        async with ctx.display.spinner():
            ctx.display.spinner_set("wf_tag", "running workflow",
                                    priority=Priority.NORMAL, truncate=Truncate.TAIL)
            results = await runner.run(args=user_args)
    except Exception as e:
        ctx.display.error(f"Workflow failed: {e}")
        return CommandResult.CONTINUE

    ctx.app.wf_renderer.summary(wf, results)
    ctx.display.info(f"Run ID: {run_id}")
    return CommandResult.CONTINUE


async def _run_bg(ctx: CommandContext, args_str: str) -> CommandResult:
    parts = args_str.split()
    if not parts:
        ctx.display.warn("Usage: /workflow run-bg <name> [positional...] [key=value...]")
        return CommandResult.CONTINUE

    name = parts[0]

    registry = ctx.app.workflow_registry
    wf = registry.get(name)
    if wf is None:
        ctx.display.warn(f"Workflow '{name}' not found.")
        return CommandResult.CONTINUE

    try:
        user_args = parse_args(wf.params, parts[1:])
    except ValueError as e:
        ctx.display.warn(str(e))
        return CommandResult.CONTINUE

    store = WorkflowRunStore()
    run_id = store.create(
        workflow_name=name,
        workflow_path=str(wf.path),
        args=user_args,
    )

    asyncio.create_task(_bg_task(wf, run_id, store, user_args, ctx.app.agent))
    ctx.display.info(
        f"Workflow '{name}' started in background (run_id: {run_id})"
    )
    return CommandResult.CONTINUE


async def _status(ctx: CommandContext, args_str: str) -> CommandResult:
    store = WorkflowRunStore()
    run_id = _resolve_run_id(store, args_str.strip())
    if run_id is None:
        ctx.display.warn("No workflow runs found.")
        return CommandResult.CONTINUE

    record = store.load(run_id)
    if record is None:
        ctx.display.warn(f"Run '{run_id}' not found.")
        return CommandResult.CONTINUE

    status = record.get("status", "unknown")

    # Liveness check: if JSON says running but PID is dead → crashed
    if status == "running":
        pid = record.get("pid")
        if pid and not store.is_alive(run_id):
            status = "crashed"

    lines = [
        f"Run:       {run_id}",
        f"Workflow:  {record.get('workflow_name', '?')}",
        f"Status:    {status}",
        f"Started:   {record.get('started_at', '?')}",
    ]

    if record.get("finished_at"):
        lines.append(f"Finished:  {record['finished_at']}")
    if record.get("wall_duration") is not None:
        lines.append(f"Duration:  {record['wall_duration']:.1f}s")
    if record.get("pid"):
        alive = "alive" if store.is_alive(run_id) else "dead"
        lines.append(f"PID:       {record['pid']} ({alive})")

    results = record.get("results", [])
    if results:
        done = sum(
            1
            for r in results
            if r.get("status") == "done" and r.get("exit_code") == 0
        )
        failed = sum(1 for r in results if r.get("exit_code", 0) != 0)
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        lines.append(f"Nodes:     {done} done, {failed} failed, {skipped} skipped")

    if status == "crashed":
        lines.append("")
        lines.append("Process died unexpectedly. Check the log file.")

    ctx.display.info("\n".join(lines))
    return CommandResult.CONTINUE


async def _result(ctx: CommandContext, args_str: str) -> CommandResult:
    store = WorkflowRunStore()
    run_id = _resolve_run_id(store, args_str.strip())
    if run_id is None:
        ctx.display.warn("No workflow runs found.")
        return CommandResult.CONTINUE

    record = store.load(run_id)
    if record is None:
        ctx.display.warn(f"Run '{run_id}' not found.")
        return CommandResult.CONTINUE

    # Raw JSON mode
    if "json" in args_str.lower():
        ctx.display.info(json.dumps(record, indent=2, ensure_ascii=False))
        return CommandResult.CONTINUE

    # Text mode
    results = [NodeResult(**r) for r in record.get("results", [])]
    name = record.get("workflow_name", "?")

    header = [
        f"Run:       {run_id}",
        f"Status:    {record.get('status', '?')}",
    ]
    if record.get("wall_duration") is not None:
        header.append(f"Duration:  {record['wall_duration']:.1f}s")
    header.append("")

    if results:
        header.append(detailed_summarize(name, results, max_lines=9999))
    else:
        header.append("No node results yet.")

    ctx.display.print("\n".join(header))
    return CommandResult.CONTINUE


async def _runs(ctx: CommandContext, args_str: str) -> CommandResult:
    store = WorkflowRunStore()
    runs = store.list_recent()

    if not runs:
        ctx.display.warn("No workflow runs found.")
        return CommandResult.CONTINUE

    lines = []
    for r in runs:
        rid = r.get("run_id", "?")
        rname = r.get("workflow_name", "?")
        status = r.get("status", "?")
        started = r.get("started_at", "?")[:19]  # trim microseconds

        alive_tag = ""
        if status == "running" and r.get("pid"):
            alive_tag = " (alive)" if store.is_alive(rid) else " (dead)"

        lines.append(f"  {rid}  {rname:20s}  {status}{alive_tag}  {started}")

    ctx.display.info("\n".join(lines))
    return CommandResult.CONTINUE


# ── Interactive menu ──────────────────────────────────────


async def _interactive_menu(ctx: CommandContext, group: Command) -> CommandResult:
    registry = ctx.app.workflow_registry
    workflows = registry.list()
    if not workflows:
        ctx.display.info("No workflows found.")
        return CommandResult.CONTINUE

    choices = [
        Choice(title=wf.name, value=wf.name, description=wf.description[:60])
        for wf in workflows
    ]
    choices.append(Choice(title="Recent runs", value="__runs__"))
    choices.append(Choice(title="Back", value="__back__"))
    chosen = await select("Workflows:", choices)
    if chosen is None or chosen == "__back__":
        return CommandResult.CONTINUE
    if chosen == "__runs__":
        return await _runs(ctx, "")

    action = await select(
        f"Workflow '{chosen}':",
        [
            Choice(title="Run", value="run"),
            Choice(title="Run (background)", value="run-bg"),
            Choice(title="Show details", value="show"),
            Choice(title="Status (latest)", value="status"),
            Choice(title="Back", value="back"),
        ],
    )
    if action is None or action == "back":
        return CommandResult.CONTINUE
    if action == "run":
        ctx.display.set_pending_input(f"/workflow run {chosen}")
        return CommandResult.CONTINUE
    if action == "run-bg":
        ctx.display.set_pending_input(f"/workflow run-bg {chosen}")
        return CommandResult.CONTINUE
    if action == "status":
        ctx.display.set_pending_input(f"/workflow status {chosen}")
        return CommandResult.CONTINUE
    return await _show(ctx, chosen)


# ── Module-level helpers ──────────────────────────────────


async def _bg_task(
    wf: object,
    run_id: str,
    store: WorkflowRunStore,
    user_args: dict[str, str],
    parent_agent: object,
) -> None:
    """Background asyncio task — runs the DAG and persists results."""
    from datetime import datetime

    runner = DAGRunner(wf, parent_agent=parent_agent, run_id=run_id, run_store=store, timeout=wf.timeout)
    try:
        await runner.run(args=user_args)
    except Exception:
        record = store.load(run_id)
        if record and record.get("status") == "running":
            store.update(run_id, [], "failed", datetime.now().isoformat())


def _resolve_run_id(store: WorkflowRunStore, raw: str) -> str | None:
    """Resolve a run_id from user input — handles run_id, workflow name, or empty."""
    if raw.startswith("wf_"):
        return raw
    if raw:
        record = store.find_latest(workflow_name=raw)
        if record:
            return record["run_id"]
        return store.resolve_run_id(raw)
    return store.resolve_run_id(None)


# ── Command definition ────────────────────────────────────


command = Command(
    name="/workflow",
    description="Manage and run YAML workflows",
    aliases=("workflow",),
    subcommands=(
        Subcommand(("list", "ls"), "List available workflows", _list),
        Subcommand("show", "Show workflow details", _show),
        Subcommand("run", "Run a workflow", _run),
        Subcommand(("run-bg", "run_bg"), "Run in background", _run_bg),
        Subcommand("status", "Check run status", _status),
        Subcommand("result", "View run results", _result),
        Subcommand("runs", "List recent runs", _runs),
    ),
    menu=_interactive_menu,
)

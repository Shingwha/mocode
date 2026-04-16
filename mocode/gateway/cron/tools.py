"""Cron tool - agent-facing scheduled task management

v0.2 adaptation:
- register_cron_tools() now accepts (registry, scheduler) for instance-scoped registration
"""

from ...tool import Tool, ToolRegistry
from ..tools import (
    _current_scheduler,
    _current_session_key,
    _current_channel,
    _current_chat_id,
)
from .scheduler import CronScheduler
from .types import CronJob, ScheduleMode


def register_cron_tools(registry: ToolRegistry, scheduler: CronScheduler) -> None:
    """Register the cron tool onto the given registry instance."""

    def cron_handler(args: dict) -> str:
        action = args.get("action", "")
        scheduler = _current_scheduler.get()
        if scheduler is None:
            return "Error: cron tool is not available in the current context"

        if action == "create":
            return _action_create(scheduler, args)
        elif action == "list":
            return _action_list(scheduler)
        elif action == "delete":
            return _action_delete(scheduler, args)
        elif action == "info":
            return _action_info(scheduler, args)
        else:
            return f"Error: unknown action '{action}'. Use: create, list, delete, info"

    registry.register(Tool(
        name="cron",
        description=(
            "Manage scheduled tasks. Actions: "
            "create (schedule a new task), list (show your tasks), "
            "delete (remove a task), info (show task details)."
        ),
        params={
            "action": {
                "type": "string",
                "description": "Action to perform: create, list, delete, info",
                "enum": ["create", "list", "delete", "info"],
            },
            "name": {
                "type": "string",
                "description": "Human-readable job name (for create)",
            },
            "prompt": {
                "type": "string",
                "description": "Instruction for the agent when the job fires (for create)",
            },
            "interval_s": {
                "type": "integer",
                "description": "Run every N seconds (interval mode)",
            },
            "cron_expr": {
                "type": "string",
                "description": "5-field cron expression, e.g. '*/5 * * * *' (cron_expr mode)",
            },
            "timezone": {
                "type": "string",
                "description": "Timezone for cron expressions (default: UTC)",
            },
            "one_shot": {
                "type": "boolean",
                "description": "Run once then auto-delete (default: false)",
            },
            "deliver": {
                "type": "boolean",
                "description": "Send result to user via channel (default: true)",
            },
            "job_id": {
                "type": "string",
                "description": "Job ID (for delete/info)",
            },
        },
        func=cron_handler,
    ))


def _action_create(scheduler: CronScheduler, args: dict) -> str:
    name = args.get("name", "Untitled task")
    prompt = args.get("prompt", "")
    if not prompt:
        return "Error: prompt is required for create"

    interval_s = args.get("interval_s", 0)
    cron_expr = args.get("cron_expr", "")
    one_shot = args.get("one_shot", False)
    timezone = args.get("timezone", "UTC")
    deliver = args.get("deliver", True)

    if not interval_s and not cron_expr and not one_shot:
        return "Error: specify interval_s, cron_expr, or one_shot=true"

    if cron_expr:
        schedule_mode = ScheduleMode.CRON_EXPR
    elif one_shot:
        schedule_mode = ScheduleMode.ONE_SHOT
    else:
        schedule_mode = ScheduleMode.INTERVAL

    job = CronJob(
        name=name,
        user_session_key=_current_session_key.get(),
        channel=_current_channel.get(),
        chat_id=_current_chat_id.get(),
        prompt=prompt,
        schedule_mode=schedule_mode,
        interval_s=int(interval_s) if interval_s else 0,
        cron_expr=str(cron_expr),
        timezone=str(timezone),
        deliver=bool(deliver),
    )
    job.next_run_at = scheduler.compute_next_run(job)
    scheduler.create_job(job)

    mode_label = schedule_mode.value
    return f"Created cron job '{name}' (id={job.id}, mode={mode_label}, next_run={job.next_run_at})"


def _action_list(scheduler: CronScheduler) -> str:
    session_key = _current_session_key.get()
    jobs = scheduler.list_jobs(session_key)
    if not jobs:
        return "No scheduled tasks found."

    lines = []
    for j in jobs:
        status = "enabled" if j.enabled else "disabled"
        lines.append(
            f"- [{j.id}] {j.name} | mode={j.schedule_mode.value} | "
            f"runs={j.run_count} | {status}"
        )
    return "Scheduled tasks:\n" + "\n".join(lines)


def _action_delete(scheduler: CronScheduler, args: dict) -> str:
    job_id = args.get("job_id", "")
    if not job_id:
        return "Error: job_id is required for delete"

    session_key = _current_session_key.get()
    job = scheduler.get_job(job_id)
    if job is None:
        return f"Error: job '{job_id}' not found"
    if job.user_session_key != session_key:
        return "Error: job does not belong to current user"

    scheduler.delete_job(job_id)
    return f"Deleted cron job '{job.name}' (id={job_id})"


def _action_info(scheduler: CronScheduler, args: dict) -> str:
    job_id = args.get("job_id", "")
    if not job_id:
        return "Error: job_id is required for info"

    job = scheduler.get_job(job_id)
    if job is None:
        return f"Error: job '{job_id}' not found"

    d = job.to_dict()
    lines = [f"{k}: {v}" for k, v in d.items()]
    return "Job details:\n" + "\n".join(lines)

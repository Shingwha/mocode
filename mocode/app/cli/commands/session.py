"""Session commands — /resume, /export."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...session import SessionManager
from .. import dialogs
from . import CONTINUE, Command, CommandContext, CommandResult


# ── /export ───────────────────────────────────────────────


async def _export(ctx: CommandContext) -> CommandResult:
    session = ctx.app.session_mgr.get_active()
    if session is None or not session.messages:
        ctx.display.warn("No active session to export.")
        return CONTINUE

    fmt = ctx.args.strip().lower() or "json"
    system_prompt = ctx.app.agent.system_prompt
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "md":
        path = Path.cwd() / f"session_{ts}.md"
        ctx.app.session_mgr.export_to_md(
            session, path, system_prompt=system_prompt
        )
    else:
        path = Path.cwd() / f"session_{ts}.json"
        ctx.app.session_mgr.export_to_file(
            session, path, system_prompt=system_prompt
        )

    ctx.display.info(f"Exported {len(session.messages)} msgs → {path}")
    return CONTINUE


# ── /resume ───────────────────────────────────────────────

MAX_RESUME_CHOICES = 20


async def _resume_from_file(ctx: CommandContext, arg: str):
    """Resume from a portable JSON file."""
    path = Path(arg.strip('"').strip("'")).expanduser()
    result = SessionManager.import_from_file(path)
    if result is None:
        ctx.display.warn(f"Invalid or missing session file: {arg}")
        return
    messages, _title = result
    ctx.app.resume_from_file(messages)
    user_count = sum(1 for m in messages if m.get("role") == "user")
    ctx.display.info(
        f"Resumed {len(messages)} msgs ({user_count} user turns) from {path.name}"
    )


async def _resume_interactive(ctx: CommandContext):
    """Interactive picker over recent sessions."""
    sessions = ctx.app.session_mgr.list()
    if not sessions:
        ctx.display.info("No sessions found.")
        return

    active_id = ctx.app.session_mgr.active_id
    candidates = [s for s in sessions if s.id != active_id][:MAX_RESUME_CHOICES]
    truncated = len([s for s in sessions if s.id != active_id]) > MAX_RESUME_CHOICES

    if not candidates:
        ctx.display.info("No other sessions to resume.")
        return

    choices = [
        dialogs.Choice(
            title=(s.title or "Untitled")[:60],
            value=s.id,
            description=f"{s.updated_at[:10]} · {len(s.messages)} msgs",
        )
        for s in candidates
    ]

    if truncated:
        choices.append(
            dialogs.Choice(
                title="(older sessions omitted — use /resume <file.json> to load one)",
                value="__truncated__",
                disabled=True,
            )
        )

    chosen = await dialogs.select("Resume a session:", choices)
    if chosen is None or chosen == "__truncated__":
        return

    session = ctx.app.session_mgr.resume(chosen)
    if session is None:
        ctx.display.error(f"Session not found: {chosen}")
        return

    ctx.app.resume_session(session)
    user_count = sum(1 for m in session.messages if m.get("role") == "user")
    ctx.display.info(
        f"Resumed {session.id} ({len(session.messages)} msgs, {user_count} user turns)"
    )


async def _resume(ctx: CommandContext) -> CommandResult:
    if ctx.args:
        await _resume_from_file(ctx, ctx.args)
    else:
        await _resume_interactive(ctx)
    return CONTINUE


# ── Commands list ─────────────────────────────────────────

commands: list[Command] = [
    Command("/export", "Export conversation to a file (json|md)", handler=_export),
    Command("/resume", "Browse and resume sessions", handler=_resume),
]

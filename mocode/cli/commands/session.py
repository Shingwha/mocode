"""Session commands — /resume, /export.

Both are terminal-facing: picking a session needs a picker, so they ask the
frontend for messages and hand the switching itself back to the runtime.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...host.command import CONTINUE, Command, CommandContext, CommandResult
from ...host.session import SessionManager
from .. import dialogs


# ── /export ───────────────────────────────────────────────


async def _export(ctx: CommandContext) -> CommandResult:
    session = ctx.app.sessions.get_active()
    if session is None or not session.messages:
        if ctx.frontend:
            ctx.frontend.warn("No active session to export.")
        return CONTINUE

    fmt = ctx.args.strip().lower() or "json"
    system_prompt = ctx.app.agent.system_prompt
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "md":
        path = Path.cwd() / f"session_{ts}.md"
        ctx.app.sessions.export_to_md(session, path, system_prompt=system_prompt)
    else:
        path = Path.cwd() / f"session_{ts}.json"
        ctx.app.sessions.export_to_file(session, path, system_prompt=system_prompt)

    if ctx.frontend:
        ctx.frontend.info(f"Exported {len(session.messages)} msgs → {path}")
    return CONTINUE


# ── /resume ───────────────────────────────────────────────

MAX_RESUME_CHOICES = 20


async def _resume_from_file(ctx: CommandContext, arg: str) -> None:
    path = Path(arg.strip('"').strip("'")).expanduser()
    result = SessionManager.import_from_file(path)
    if result is None:
        if ctx.frontend:
            ctx.frontend.warn(f"Invalid or missing session file: {arg}")
        return

    messages, _title = result
    ctx.app.start_session(messages)
    ctx.redraw()
    user_count = sum(1 for m in messages if m.get("role") == "user")
    if ctx.frontend:
        ctx.frontend.info(
            f"Resumed {len(messages)} msgs ({user_count} user turns) from {path.name}"
        )


async def _resume_interactive(ctx: CommandContext) -> None:
    """Interactive picker over recent sessions. Caller guarantees a frontend."""
    sessions = ctx.app.sessions.list()
    if not sessions:
        if ctx.frontend:
            ctx.frontend.info("No sessions found.")
        return

    active_id = ctx.app.sessions.active_id
    others = [s for s in sessions if s.id != active_id]
    candidates = others[:MAX_RESUME_CHOICES]

    if not candidates:
        if ctx.frontend:
            ctx.frontend.info("No other sessions to resume.")
        return

    choices = [
        dialogs.Choice(
            title=(s.title or "Untitled")[:60],
            value=s.id,
            description=f"{s.updated_at[:10]} · {len(s.messages)} msgs",
        )
        for s in candidates
    ]
    if len(others) > MAX_RESUME_CHOICES:
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

    session = ctx.app.sessions.resume(chosen)
    if session is None:
        if ctx.frontend:
            ctx.frontend.error(f"Session not found: {chosen}")
        return

    ctx.app.use_session(session)
    ctx.redraw()
    user_count = sum(1 for m in session.messages if m.get("role") == "user")
    if ctx.frontend:
        ctx.frontend.info(
            f"Resumed {session.id} ({len(session.messages)} msgs, {user_count} user turns)"
        )


async def _resume(ctx: CommandContext) -> CommandResult:
    if ctx.args:
        await _resume_from_file(ctx, ctx.args)
    elif ctx.frontend is not None:
        await _resume_interactive(ctx)
    return CONTINUE


# ── Commands list ─────────────────────────────────────────

commands: list[Command] = [
    Command("/export", "Export conversation to a file (json|md)", handler=_export),
    Command("/resume", "Browse and resume sessions", handler=_resume),
]

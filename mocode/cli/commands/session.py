"""Session commands — /resume, /export.

Both are terminal-facing: resuming needs a picker, so it asks the frontend for a
choice and hands the switching itself back to the conversation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...host.command import CONTINUE, Command, CommandContext, CommandResult
from ...host.session import export_session, export_session_md, load_session_file
from .. import dialogs


# ── /export ───────────────────────────────────────────────


async def _export(ctx: CommandContext) -> CommandResult:
    conversation = ctx.conversation
    session = conversation.session()
    if not session.messages:
        await conversation.notify("No active session to export.", level="warn")
        return CONTINUE

    fmt = ctx.args.strip().lower() or "json"
    system_prompt = conversation.agent.system_prompt
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Into the project the conversation works in, not the process directory.
    path = conversation.cwd / f"session_{ts}.{fmt if fmt == 'md' else 'json'}"

    if fmt == "md":
        export_session_md(session, path, system_prompt=system_prompt)
    else:
        export_session(path, session, system_prompt=system_prompt)

    await conversation.notify(f"Exported {len(session.messages)} msgs → {path}")
    return CONTINUE


# ── /resume ───────────────────────────────────────────────

MAX_RESUME_CHOICES = 20


async def _resume_from_file(ctx: CommandContext, arg: str) -> None:
    path = Path(arg.strip('"').strip("'")).expanduser()
    result = load_session_file(path)
    if result is None:
        await ctx.conversation.notify(
            f"Invalid or missing session file: {arg}", level="warn"
        )
        return

    messages, _title = result
    await ctx.conversation.start(messages)
    user_count = sum(1 for m in messages if m.get("role") == "user")
    await ctx.conversation.notify(
        f"Resumed {len(messages)} msgs ({user_count} user turns) from {path.name}"
    )


async def _resume_interactive(ctx: CommandContext) -> None:
    """Interactive picker over recent sessions."""
    conversation = ctx.conversation
    sessions = conversation.list_sessions()
    if not sessions:
        await conversation.notify("No sessions found.")
        return

    others = [s for s in sessions if s.id != conversation.id]
    candidates = others[:MAX_RESUME_CHOICES]

    if not candidates:
        await conversation.notify("No other sessions to resume.")
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

    session = next((s for s in candidates if s.id == chosen), None)
    if session is None:
        await conversation.notify(f"Session not found: {chosen}", level="error")
        return

    await conversation.resume(session)
    user_count = sum(1 for m in session.messages if m.get("role") == "user")
    await conversation.notify(
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

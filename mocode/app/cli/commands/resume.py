"""Resume command — resume a session from picker or file."""

from __future__ import annotations

import json
from pathlib import Path

from ..prompts import Choice, select

from . import CommandContext, CommandResult

MAX_RESUME_CHOICES = 20


class ResumeCommand:
    name = "/resume"
    description = "Browse and resume sessions"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        arg = ctx.args

        if arg:
            await self._resume_from_arg(ctx, arg)
        else:
            await self._resume_interactive(ctx)

        return CommandResult.CONTINUE

    async def _resume_from_arg(self, ctx: CommandContext, arg: str):
        """Resume from a file path."""
        path = Path(arg.strip('"').strip("'")).expanduser()
        if not (path.suffix == ".json" and path.exists()):
            ctx.display.warn(f"File not found: {arg}")
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            ctx.display.error(f"Failed to read JSON: {e}")
            return

        if not isinstance(data, dict) or "messages" not in data:
            ctx.display.error("Invalid format: expected {system_prompt, messages}")
            return
        messages = data["messages"]

        ctx.app.replace_messages(messages)
        user_count = sum(1 for m in messages if m.get("role") == "user")
        ctx.display.info(
            f"Resumed {len(messages)} msgs ({user_count} user turns) from {path.name}"
        )

    async def _resume_interactive(self, ctx: CommandContext):
        """Interactive picker over recent sessions."""
        sessions = ctx.app.session_mgr.list()

        if not sessions:
            ctx.display.info("No sessions found.")
            return

        active_id = ctx.app.session_mgr.active_id
        # Sort by updated_at descending, cap at MAX_RESUME_CHOICES
        candidates = [s for s in sessions if s.id != active_id][:MAX_RESUME_CHOICES]
        truncated = len([s for s in sessions if s.id != active_id]) > MAX_RESUME_CHOICES

        if not candidates:
            ctx.display.info("No other sessions to resume.")
            return

        choices = [
            Choice(
                title=(s.title or "Untitled")[:60],
                value=s.id,
                description=f"{s.updated_at[:10]} · {len(s.messages)} msgs",
            )
            for s in candidates
        ]

        if truncated:
            choices.append(
                Choice(
                    title="(older sessions omitted — use /resume <file.json> to load one)",
                    value="__truncated__",
                    disabled=True,
                )
            )

        chosen = await select("Resume a session:", choices)
        if chosen is None or chosen == "__truncated__":
            return

        session = ctx.app.session_mgr.resume(chosen)
        if session is None:
            ctx.display.error(f"Session not found: {chosen}")
            return

        ctx.app.replace_messages(session.messages)
        user_count = sum(1 for m in session.messages if m.get("role") == "user")
        ctx.display.info(
            f"Resumed {session.id} ({len(session.messages)} msgs, {user_count} user turns)"
        )

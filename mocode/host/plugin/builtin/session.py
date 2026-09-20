"""session plugin — the conversation's session, as files.

``/export`` writes the current session out (JSON to resume later, Markdown to
read) and ``/clear`` starts a fresh one in the same project. Both need nothing
but a conversation, so every frontend gets them; the interactive half of
session management — browsing what to resume — needs a picker and stays with
the frontend that offers one (the terminal's ``/resume``).
"""

from __future__ import annotations

from datetime import datetime

from ...command import CONTINUE, Command, CommandContext, CommandResult
from ...export import export_session, export_session_md
from ..base import Plugin
from ..context import HostContext


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


async def _clear(ctx: CommandContext) -> CommandResult:
    await ctx.conversation.new_session()
    return CONTINUE


class SessionPlugin(Plugin):
    name = "session"
    description = "Session files: /export /clear"

    def build(self, ctx: HostContext) -> None:
        ctx.register(
            Command("/export", "Export conversation to a file (json|md)", handler=_export),
            Command("/clear", "Clear the current conversation", handler=_clear),
        )


PLUGIN = SessionPlugin()

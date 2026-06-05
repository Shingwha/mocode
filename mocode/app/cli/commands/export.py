"""Export command — saves the active session to a portable JSON or Markdown file."""

from datetime import datetime
from pathlib import Path

from . import CommandContext, CommandResult


class ExportCommand:
    name = "/export"
    description = "Export conversation to a file (json|md)"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        session = ctx.app.session_mgr.get_active()
        if session is None or not session.messages:
            ctx.display.warn("No active session to export.")
            return CommandResult.CONTINUE

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
        return CommandResult.CONTINUE

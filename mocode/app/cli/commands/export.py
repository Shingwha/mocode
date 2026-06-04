"""Export command — saves the active session to a portable JSON file."""

from datetime import datetime
from pathlib import Path

from . import CommandContext, CommandResult


class ExportCommand:
    name = "/export"
    description = "Export conversation to a file"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        session = ctx.app.session_mgr.get_active()
        if session is None or not session.messages:
            ctx.display.warn("No active session to export.")
            return CommandResult.CONTINUE

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.cwd() / f"session_{ts}.json"
        ctx.app.session_mgr.export_to_file(
            session, path, system_prompt=ctx.app.agent.system_prompt
        )
        ctx.display.info(f"Exported {len(session.messages)} msgs → {path}")
        return CommandResult.CONTINUE

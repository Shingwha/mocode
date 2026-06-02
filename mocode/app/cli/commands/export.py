"""Export command — saves conversation to a JSON file."""

import json
from datetime import datetime
from pathlib import Path

from . import Command, CommandContext, CommandResult


class ExportCommand:
    name = "/export"
    description = "Export conversation to a file"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.cwd() / f"session_{ts}.json"
        path.write_text(
            json.dumps(ctx.app.agent.messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ctx.display.info(f"Exported {len(ctx.app.agent.messages)} msgs → {path}")
        return CommandResult.CONTINUE

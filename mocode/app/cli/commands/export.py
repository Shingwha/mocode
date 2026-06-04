"""Export command — saves conversation to a JSON file."""

import json
from datetime import datetime
from pathlib import Path

from . import CommandContext, CommandResult


class ExportCommand:
    name = "/export"
    description = "Export conversation to a file"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.cwd() / f"session_{ts}.json"
        data = {
            "system_prompt": ctx.app.agent.system_prompt,
            "messages": ctx.app.agent.messages,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        sp_len = len(ctx.app.agent.system_prompt)
        ctx.display.info(
            f"Exported {len(ctx.app.agent.messages)} msgs (prompt {sp_len} chars) → {path}"
        )
        return CommandResult.CONTINUE

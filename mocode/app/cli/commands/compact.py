"""/compact command — manually trigger context compression."""

from __future__ import annotations

from ....tools.compact import compact_messages
from . import CommandContext, CommandResult


class CompactCommand:
    name = "/compact"
    description = "Compress conversation history to free up context window"
    aliases = ("compact",)

    async def run(self, ctx: CommandContext) -> CommandResult:
        agent = ctx.app.agent
        if not agent.messages:
            ctx.display.info("No messages to compact.")
            return CommandResult.CONTINUE

        old_count = len(agent.messages)
        ctx.display.info("Compacting conversation...")

        new_messages = await compact_messages(agent.provider, agent.messages)
        agent.messages.clear()
        agent.messages.extend(new_messages)

        new_count = len(new_messages)
        ctx.display.info(f"Compacted: {old_count} → {new_count} messages")

        ctx.app._save_current_session()
        return CommandResult.CONTINUE

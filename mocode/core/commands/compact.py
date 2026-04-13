"""Context compact command"""

import asyncio

from .base import Command, CommandContext, command
from .result import CommandResult


@command("/compact", description="Compress conversation context")
class CompactCommand(Command):
    """Compress conversation context to save tokens"""

    def execute(self, ctx: CommandContext) -> CommandResult:
        args = ctx.args.strip()
        if args == "status":
            return self._show_status(ctx)
        return self._do_compact(ctx)

    def _show_status(self, ctx: CommandContext) -> CommandResult:
        """Show token usage and compact status"""
        client = ctx.client
        compact_config = client.config.compact
        model = client.current_model
        usage = client.token_usage

        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            window = client.compact_manager.get_context_window(model)

            return CommandResult(data={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "window": window,
                "compact_config": {
                    "threshold": compact_config.threshold,
                    "keep_recent_turns": compact_config.keep_recent_turns,
                    "enabled": compact_config.enabled,
                },
                "message_count": len(client.messages),
            })

        return CommandResult(
            message="No token usage data yet. Send a message first.",
            data={"message_count": len(client.messages)},
        )

    def _do_compact(self, ctx: CommandContext) -> CommandResult:
        """Trigger manual compaction"""
        msg_count = len(ctx.client.messages)

        if msg_count < 4:
            return CommandResult(message="Not enough messages to compact (need at least 4).")

        if ctx.loop is None:
            return CommandResult(success=False, message="Cannot compact: no event loop available.")

        future = asyncio.run_coroutine_threadsafe(ctx.client.compact(), ctx.loop)
        try:
            result = future.result(timeout=30)
            old = result.get("old_count", msg_count)
            new = result.get("new_count", "?")
            return CommandResult(
                success=True,
                message=f"Compacted: {old} -> {new} messages",
                data=result,
            )
        except Exception as e:
            return CommandResult(success=False, message=f"Compact failed: {e}")

"""Context compact command"""

import asyncio

from .base import Command, CommandContext, command


@command("/compact", description="Compress conversation context")
class CompactCommand(Command):
    """Compress conversation context to save tokens"""

    def execute(self, ctx: CommandContext) -> bool:
        args = ctx.args.strip()
        if args == "status":
            return self._show_status(ctx)
        return self._do_compact(ctx)

    def _show_status(self, ctx: CommandContext) -> bool:
        """Show token usage and compact status"""
        client = ctx.client

        # Get compact config
        compact_config = client.config.compact
        model = client.current_model

        # Get token usage from agent
        usage = client.token_usage

        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            # Estimate context window
            window = client.compact_manager.get_context_window(model)

            pct = (prompt_tokens / window * 100) if window > 0 else 0
            threshold_pct = compact_config.threshold * 100

            self._info(ctx, f"Token usage for {model}:")
            self._output(ctx, f"  Prompt tokens:   {prompt_tokens:,} / {window:,} ({pct:.1f}%)")
            self._output(ctx, f"  Completion:      {completion_tokens:,}")
            self._output(ctx, f"  Auto-compact at: {threshold_pct:.0f}% ({int(window * compact_config.threshold):,} tokens)")
            self._output(ctx, f"  Keep recent:     {compact_config.keep_recent_turns} turns")
            self._output(ctx, f"  Enabled:         {compact_config.enabled}")
        else:
            self._info(ctx, "No token usage data yet. Send a message first.")

        msg_count = len(client.messages)
        self._output(ctx, f"  Messages:        {msg_count}")
        return True

    def _do_compact(self, ctx: CommandContext) -> bool:
        """Trigger manual compaction"""
        msg_count = len(ctx.client.messages)

        if msg_count < 4:
            self._info(ctx, "Not enough messages to compact (need at least 4).")
            return True

        if ctx.loop is None:
            self._error(ctx, "Cannot compact: no event loop available.")
            return True

        future = asyncio.run_coroutine_threadsafe(ctx.client.compact(), ctx.loop)
        try:
            result = future.result(timeout=30)
            old = result.get("old_count", msg_count)
            new = result.get("new_count", "?")
            self._success(ctx, f"Compacted: {old} -> {new} messages")
        except Exception as e:
            self._error(ctx, f"Compact failed: {e}")
        return True

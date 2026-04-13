"""Dream command - manual dream cycle and snapshot management"""

import asyncio

from .base import Command, CommandContext, command
from .result import CommandResult


@command("/dream", description="Trigger dream cycle or manage snapshots")
class DreamCommand(Command):
    """Trigger dream cycle for offline memory consolidation"""

    def execute(self, ctx: CommandContext) -> CommandResult:
        args = ctx.args.strip()
        if args == "status":
            return self._show_status(ctx)
        if args == "log":
            return self._show_log(ctx)
        if args.startswith("restore "):
            return self._restore_snapshot_by_id(ctx, args[8:].strip())
        if args == "restore":
            return self._restore_snapshot_interactive(ctx)
        return self._do_dream(ctx)

    def _show_status(self, ctx: CommandContext) -> CommandResult:
        """Show dream system status."""
        status = ctx.client.dream_manager.get_status()
        return CommandResult(data={"status": status})

    def _do_dream(self, ctx: CommandContext) -> CommandResult:
        """Manually trigger a dream cycle."""
        status = ctx.client.dream_manager.get_status()

        if status["pending_summaries"] == 0:
            return CommandResult(message="No pending summaries. Have a conversation first.")

        if ctx.loop is None:
            return CommandResult(success=False, message="Cannot dream: no event loop available.")

        future = asyncio.run_coroutine_threadsafe(ctx.client.dream(), ctx.loop)
        try:
            result = future.result(timeout=60)
            if result.get("skipped"):
                return CommandResult(message="Dream skipped: no actionable insights found.")
            return CommandResult(
                success=True,
                message=(
                    f"Dream complete: {result['summaries_processed']} summaries, "
                    f"{result['edits_made']} edits, "
                    f"{result['tool_calls_made']} tool calls"
                ),
                data=result,
            )
        except Exception as e:
            return CommandResult(success=False, message=f"Dream failed: {e}")

    def _show_log(self, ctx: CommandContext) -> CommandResult:
        """Return dream snapshots list."""
        snapshots = ctx.client.dream_manager.list_snapshots()

        if not snapshots:
            return CommandResult(message="No dream snapshots yet.")

        return CommandResult(data={"snapshots": snapshots[:20], "action": "log"})

    def _restore_snapshot_by_id(self, ctx: CommandContext, snap_id: str) -> CommandResult:
        """Restore a specific snapshot by ID."""
        if ctx.client.dream_manager.restore_snapshot(snap_id):
            ctx.client.rebuild_system_prompt()
            return CommandResult(success=True, message=f"Restored snapshot: {snap_id}")
        return CommandResult(success=False, message=f"Failed to restore snapshot: {snap_id}")

    def _restore_snapshot_interactive(self, ctx: CommandContext) -> CommandResult:
        """Return snapshots for interactive selection."""
        snapshots = ctx.client.dream_manager.list_snapshots()

        if not snapshots:
            return CommandResult(message="No snapshots to restore.")

        return CommandResult(data={"snapshots": snapshots, "action": "restore"})

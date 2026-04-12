"""Dream command - manual dream cycle and snapshot management"""

import asyncio

from .base import Command, CommandContext, command
from ..ui.prompt import select, MenuItem, is_cancelled, MenuAction


@command("/dream", description="Trigger dream cycle or manage snapshots")
class DreamCommand(Command):
    """Trigger dream cycle for offline memory consolidation"""

    def execute(self, ctx: CommandContext) -> bool:
        args = ctx.args.strip()
        if args == "status":
            return self._show_status(ctx)
        if args == "log":
            return self._show_log(ctx)
        if args == "restore":
            return self._restore_snapshot(ctx)
        return self._do_dream(ctx)

    def _show_status(self, ctx: CommandContext) -> bool:
        """Show dream system status."""
        status = ctx.client.dream_manager.get_status()

        self._info(ctx, "Dream system status:")
        self._output(ctx, f"  Enabled:           {status['enabled']}")
        self._output(ctx, f"  Interval:          {status['interval_seconds']}s")
        self._output(ctx, f"  Total processed:   {status['total_processed']}")
        self._output(ctx, f"  Pending summaries: {status['pending_summaries']}")
        self._output(ctx, f"  Snapshots:         {status['snapshot_count']}")

        last = status.get("last_snapshot")
        if last:
            self._output(ctx, f"  Last snapshot:     {last['id']} ({last['created_at']})")
        return True

    def _do_dream(self, ctx: CommandContext) -> bool:
        """Manually trigger a dream cycle."""
        status = ctx.client.dream_manager.get_status()

        if status["pending_summaries"] == 0:
            self._info(ctx, "No pending summaries. Have a conversation first.")
            return True

        if ctx.loop is None:
            self._error(ctx, "Cannot dream: no event loop available.")
            return True

        future = asyncio.run_coroutine_threadsafe(
            ctx.client.dream(), ctx.loop
        )
        try:
            result = future.result(timeout=60)
            if result.get("skipped"):
                self._info(ctx, "Dream skipped: no actionable insights found.")
            else:
                self._success(ctx,
                    f"Dream complete: {result['summaries_processed']} summaries, "
                    f"{result['directives_count']} directives, "
                    f"{result['tool_calls_made']} edits"
                )
                if result.get("snapshot_id"):
                    self._output(ctx, f"  Snapshot: {result['snapshot_id']}")
        except Exception as e:
            self._error(ctx, f"Dream failed: {e}")
        return True

    def _show_log(self, ctx: CommandContext) -> bool:
        """Show dream snapshots with diff."""
        snapshots = ctx.client.dream_manager.list_snapshots()

        if not snapshots:
            self._info(ctx, "No dream snapshots yet.")
            return True

        items = snapshots[:20]  # Show last 20
        selected = self._select_from_list(
            "Select a snapshot to view",
            items,
            lambda s: (s["id"], f"{s['created_at']}  ({s['directive_count']} directives)"),
        )

        if not selected or isinstance(selected, MenuAction):
            return True

        snap = ctx.client.dream_manager.get_snapshot(selected["id"])
        if not snap:
            self._error(ctx, f"Snapshot not found: {selected['id']}")
            return True

        self._info(ctx, f"Snapshot: {snap['id']} ({snap['created_at']})")
        self._output(ctx, f"Trigger: {snap['trigger']}")
        self._output(ctx, f"Directives: {snap['directive_count']}")
        self._output(ctx, "")

        for name, content in snap.get("files", {}).items():
            self._output(ctx, f"--- {name} ---")
            # Show first 50 lines
            lines = content.splitlines()
            for line in lines[:50]:
                self._output(ctx, f"  {line}")
            if len(lines) > 50:
                self._output(ctx, f"  ... ({len(lines) - 50} more lines)")
            self._output(ctx, "")
        return True

    def _restore_snapshot(self, ctx: CommandContext) -> bool:
        """Restore a snapshot with confirmation."""
        snapshots = ctx.client.dream_manager.list_snapshots()

        if not snapshots:
            self._info(ctx, "No snapshots to restore.")
            return True

        selected = self._select_from_list(
            "Select snapshot to restore",
            snapshots,
            lambda s: (s["id"], f"{s['created_at']}  ({s['directive_count']} directives)"),
        )

        if not selected or isinstance(selected, MenuAction):
            return True

        if not self.confirm_delete(f"snapshot '{selected['id']}'"):
            return True

        if ctx.client.dream_manager.restore_snapshot(selected["id"]):
            self._success(ctx, f"Restored snapshot: {selected['id']}")
            ctx.client.rebuild_system_prompt()
        else:
            self._error(ctx, f"Failed to restore snapshot: {selected['id']}")
        return True

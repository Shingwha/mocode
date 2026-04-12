"""Dream Scheduler - lightweight async scheduler for automatic dream cycles"""

import asyncio
import logging

from .manager import DreamManager

logger = logging.getLogger(__name__)


class DreamScheduler:
    """Runs dream cycles on a fixed interval as a background asyncio task."""

    def __init__(self, dream_manager: DreamManager, interval_seconds: int = 7200):
        self._manager = dream_manager
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Dream scheduler started (interval: {self._interval}s)")

    async def stop(self) -> None:
        """Stop the scheduler loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Dream scheduler stopped")

    async def _run_loop(self) -> None:
        """Main loop: sleep → check for summaries → dream if needed."""
        try:
            while self._running:
                await asyncio.sleep(self._interval)

                if not self._running:
                    break

                try:
                    status = self._manager.get_status()
                    if status["pending_summaries"] > 0:
                        logger.info("Dream scheduler: triggering automatic dream cycle")
                        result = await self._manager.dream()
                        if not result.skipped:
                            logger.info(
                                f"Dream scheduler: cycle complete "
                                f"({result.edits_made} edits)"
                            )
                except Exception as e:
                    logger.error(f"Dream scheduler cycle error: {e}")
        except asyncio.CancelledError:
            pass

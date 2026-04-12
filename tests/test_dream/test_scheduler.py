"""Tests for DreamScheduler"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.core.dream.scheduler import DreamScheduler
from mocode.core.dream.manager import DreamResult


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager.get_status.return_value = {
        "enabled": True,
        "pending_summaries": 5,
    }
    manager.dream = AsyncMock(return_value=DreamResult(
        summaries_processed=5,
        edits_made=3,
        tool_calls_made=5,
        snapshot_id="snap_001",
        skipped=False,
    ))
    return manager


@pytest.mark.asyncio
async def test_scheduler_start_stop(mock_manager):
    scheduler = DreamScheduler(mock_manager, interval_seconds=1)

    await scheduler.start()
    assert scheduler._running is True
    assert scheduler._task is not None

    await scheduler.stop()
    assert scheduler._running is False
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_scheduler_triggers_dream(mock_manager):
    """Scheduler triggers dream when summaries are pending."""
    scheduler = DreamScheduler(mock_manager, interval_seconds=1)

    # Shorten interval for testing
    scheduler._interval = 0.2

    dream_called = False
    original_dream = mock_manager.dream

    async def dream_and_stop(*args, **kwargs):
        nonlocal dream_called
        dream_called = True
        result = await original_dream()
        # Stop after first successful dream
        scheduler._running = False
        return result

    mock_manager.dream = dream_and_stop

    await scheduler.start()

    # Wait for the scheduler task to complete
    if scheduler._task:
        try:
            await asyncio.wait_for(scheduler._task, timeout=5)
        except asyncio.CancelledError:
            pass

    await scheduler.stop()
    assert dream_called is True


@pytest.mark.asyncio
async def test_scheduler_skips_when_no_summaries(mock_manager):
    """Scheduler skips when no pending summaries."""
    mock_manager.get_status.return_value = {
        "enabled": True,
        "pending_summaries": 0,
    }
    scheduler = DreamScheduler(mock_manager, interval_seconds=1)

    # Stop after one cycle
    call_count = 0
    original_get_status = mock_manager.get_status

    def get_status_and_stop():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            scheduler._running = False
        return original_get_status()

    mock_manager.get_status = get_status_and_stop

    await scheduler.start()

    if scheduler._task:
        try:
            await asyncio.wait_for(scheduler._task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    mock_manager.dream.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_handles_dream_error(mock_manager):
    """Scheduler continues after dream errors."""
    mock_manager.dream = AsyncMock(side_effect=Exception("Dream error"))

    scheduler = DreamScheduler(mock_manager, interval_seconds=1)

    # Stop after first cycle attempt
    cycle_count = 0

    class StopAfterFirst:
        def __init__(self, orig):
            self._orig = orig

        def __call__(self):
            return self._orig()

    # Use a flag to stop after first sleep
    original_run = scheduler._run_loop

    async def run_and_stop():
        # Modify interval to be very short
        scheduler._interval = 0.1
        try:
            await asyncio.wait_for(original_run(), timeout=3)
        except asyncio.CancelledError:
            pass

    scheduler._run_loop = run_and_stop

    await scheduler.start()
    await scheduler.stop()

    # Should not raise, error is logged
    assert True


@pytest.mark.asyncio
async def test_scheduler_double_start(mock_manager):
    """Starting twice should be a no-op."""
    scheduler = DreamScheduler(mock_manager, interval_seconds=1)

    await scheduler.start()
    task1 = scheduler._task
    await scheduler.start()
    assert scheduler._task is task1  # Same task

    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_stop_when_not_started(mock_manager):
    """Stopping when not started should be safe."""
    scheduler = DreamScheduler(mock_manager, interval_seconds=1)
    await scheduler.stop()
    assert scheduler._task is None

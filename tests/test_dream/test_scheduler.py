"""Tests for DreamScheduler"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mocode.dream.scheduler import DreamScheduler


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager.get_status.return_value = {"pending_summaries": 0, "enabled": True, "interval_seconds": 7200}
    manager.dream = AsyncMock()
    return manager


@pytest.mark.asyncio
async def test_scheduler_start_stop(mock_manager):
    scheduler = DreamScheduler(mock_manager, interval_seconds=1)
    await scheduler.start()
    assert scheduler._running
    await scheduler.stop()
    assert not scheduler._running


@pytest.mark.asyncio
async def test_scheduler_triggers_dream(mock_manager):
    mock_manager.get_status.return_value = {"pending_summaries": 5, "enabled": True}
    from mocode.dream.agent import DreamAgentResult
    mock_manager.dream.return_value = type("R", (), {"skipped": False, "edits_made": 2})()

    scheduler = DreamScheduler(mock_manager, interval_seconds=1)

    # Use a flag to know when dream was called
    dream_called = asyncio.Event()

    async def dream_side_effect():
        dream_called.set()
        return type("R", (), {"skipped": False, "edits_made": 2})()

    mock_manager.dream.side_effect = dream_side_effect

    await scheduler.start()
    await asyncio.wait_for(dream_called.wait(), timeout=3.0)
    await scheduler.stop()

    assert mock_manager.dream.called

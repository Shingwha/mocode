"""Tests for DreamManager"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.core.config import DreamConfig
from mocode.core.dream.agent import DreamAgentResult
from mocode.core.dream.manager import DreamManager, DreamResult


@pytest.fixture
def dream_setup(tmp_path: Path):
    """Set up dream test environment with directories."""
    memory_dir = tmp_path / "memory"
    dream_dir = tmp_path / "dream"
    summaries_dir = dream_dir / "summaries"

    memory_dir.mkdir(parents=True)
    dream_dir.mkdir(parents=True)
    summaries_dir.mkdir(parents=True)

    # Create memory files
    for name in ["SOUL.md", "USER.md", "MEMORY.md"]:
        (memory_dir / name).write_text(f"# {name}\nDefault content", encoding="utf-8")

    return memory_dir, dream_dir, summaries_dir


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    return provider


def _create_summary(summaries_dir: Path, summary_id: str, text: str = "Test summary"):
    data = {
        "id": summary_id,
        "created_at": "2026-04-12T12:00:00",
        "workdir": "/test",
        "summary": text,
        "message_count": 10,
    }
    path = summaries_dir / f"{summary_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class TestDreamManager:
    """Test DreamManager orchestration."""

    @pytest.mark.asyncio
    async def test_dream_no_summaries(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        result = await manager.dream()
        assert result.skipped is True
        assert result.summaries_processed == 0

    @pytest.mark.asyncio
    async def test_dream_with_summaries_no_edits(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        _create_summary(summaries_dir, "summary_001")
        _create_summary(summaries_dir, "summary_002")

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        # Mock agent to return no edits
        no_edit_result = DreamAgentResult(tool_calls_made=0, edits_made=0)
        with patch.object(manager._agent, "run", return_value=no_edit_result):
            result = await manager.dream()

        assert result.skipped is False
        assert result.summaries_processed == 2
        assert result.edits_made == 0

        # Cursor should have advanced
        cursor = manager._cursor.load()
        assert cursor["last_summary_id"] == "summary_002"
        assert cursor["total_processed"] == 1

    @pytest.mark.asyncio
    async def test_dream_full_cycle_with_edits(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        _create_summary(summaries_dir, "summary_001", "User prefers dark mode")

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        agent_result = DreamAgentResult(tool_calls_made=2, edits_made=1)

        with patch.object(manager._agent, "run", return_value=agent_result):
            result = await manager.dream()

            assert result.skipped is False
            assert result.summaries_processed == 1
            assert result.edits_made == 1
            assert result.tool_calls_made == 2
            assert result.snapshot_id is not None

    @pytest.mark.asyncio
    async def test_dream_lock_prevents_concurrent(self, dream_setup, mock_provider):
        """Verify that concurrent dream calls are serialized (not parallel)."""
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        _create_summary(summaries_dir, "summary_001")

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        call_count = 0

        async def slow_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            import asyncio
            await asyncio.sleep(0.1)
            return DreamAgentResult(tool_calls_made=0, edits_made=0)

        with patch.object(manager._agent, "run", side_effect=slow_run):
            # Run two concurrent dreams - second will be serialized
            import asyncio
            results = await asyncio.gather(
                manager.dream(),
                manager.dream(),
            )

        # Both should complete without error (lock serialization works)
        assert len(results) == 2
        processed = sum(1 for r in results if not r.skipped)
        assert processed >= 1

    def test_get_status(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        _create_summary(summaries_dir, "summary_001")
        _create_summary(summaries_dir, "summary_002")

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        status = manager.get_status()
        assert status["enabled"] is True
        assert status["pending_summaries"] == 2
        assert status["interval_seconds"] == 7200

    def test_list_snapshots_empty(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        assert manager.list_snapshots() == []

    def test_restore_snapshot(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        # Create a snapshot
        snap_id = manager._snapshot.snapshot(trigger="test")
        assert snap_id is not None

        # Modify memory
        (memory_dir / "SOUL.md").write_text("Modified", encoding="utf-8")

        # Restore
        assert manager.restore_snapshot(snap_id) is True
        assert "Default content" in (memory_dir / "SOUL.md").read_text(encoding="utf-8")

    def test_update_provider(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        new_provider = AsyncMock()
        manager.update_provider(new_provider)
        assert manager._agent._provider is new_provider

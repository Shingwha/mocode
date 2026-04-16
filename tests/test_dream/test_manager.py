"""Tests for DreamManager — v0.2"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mocode.config import DreamConfig
from mocode.dream.agent import DreamAgentResult
from mocode.dream.manager import DreamManager, DreamResult
from mocode.tool import Tool, ToolRegistry


@pytest.fixture
def dream_setup(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    dream_dir = tmp_path / "dream"
    summaries_dir = dream_dir / "summaries"

    memory_dir.mkdir(parents=True)
    dream_dir.mkdir(parents=True)
    summaries_dir.mkdir(parents=True)

    for name in ["SOUL.md", "USER.md", "MEMORY.md"]:
        (memory_dir / name).write_text(f"# {name}\nDefault content", encoding="utf-8")

    return memory_dir, dream_dir, summaries_dir


@pytest.fixture
def mock_provider():
    return AsyncMock()


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


def _make_dream_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("read", "Read", {"path": "string"}, lambda a: "content"))
    registry.register(Tool("edit", "Edit", {"path": "string", "old": "string", "new": "string"}, lambda a: "ok"))
    registry.register(Tool("append", "Append", {"path": "string", "content": "string"}, lambda a: "ok"))
    return registry


class TestDreamManager:
    @pytest.mark.asyncio
    async def test_dream_no_summaries(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            tools=_make_dream_registry(),
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
            tools=_make_dream_registry(),
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        no_edit_result = DreamAgentResult(tool_calls_made=0, edits_made=0)
        with patch.object(manager._agent, "run", return_value=no_edit_result):
            result = await manager.dream()

        assert result.skipped is False
        assert result.summaries_processed == 2
        assert result.edits_made == 0

        cursor = manager._cursor.load()
        assert cursor["last_summary_id"] == "summary_002"

    @pytest.mark.asyncio
    async def test_dream_full_cycle_with_edits(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        _create_summary(summaries_dir, "summary_001", "User prefers dark mode")

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            tools=_make_dream_registry(),
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        agent_result = DreamAgentResult(tool_calls_made=2, edits_made=1)

        with patch.object(manager._agent, "run", return_value=agent_result):
            result = await manager.dream()

            assert result.skipped is False
            assert result.summaries_processed == 1
            assert result.edits_made == 1
            assert result.snapshot_id is not None

    @pytest.mark.asyncio
    async def test_dream_lock_prevents_concurrent(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        _create_summary(summaries_dir, "summary_001")

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            tools=_make_dream_registry(),
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        async def slow_run(*args, **kwargs):
            import asyncio
            await asyncio.sleep(0.1)
            return DreamAgentResult(tool_calls_made=0, edits_made=0)

        with patch.object(manager._agent, "run", side_effect=slow_run):
            import asyncio
            results = await asyncio.gather(
                manager.dream(),
                manager.dream(),
            )

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
            tools=_make_dream_registry(),
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        status = manager.get_status()
        assert status["enabled"] is True
        assert status["pending_summaries"] == 2

    def test_restore_snapshot(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            tools=_make_dream_registry(),
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        snap_id = manager._snapshot.snapshot(trigger="test")
        assert snap_id is not None

        (memory_dir / "SOUL.md").write_text("Modified", encoding="utf-8")
        assert manager.restore_snapshot(snap_id) is True
        assert "Default content" in (memory_dir / "SOUL.md").read_text(encoding="utf-8")

    def test_update_provider(self, dream_setup, mock_provider):
        memory_dir, dream_dir, summaries_dir = dream_setup
        config = DreamConfig()

        manager = DreamManager(
            config=config,
            provider=mock_provider,
            tools=_make_dream_registry(),
            dream_dir=dream_dir,
            memory_dir=memory_dir,
        )

        new_provider = AsyncMock()
        manager.update_provider(new_provider)
        assert manager._agent._provider is new_provider

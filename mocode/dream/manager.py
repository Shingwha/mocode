"""Dream Manager - 使用 Response DTO

v0.2 改进：DreamAgent 接收 ToolRegistry 实例。
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import DreamConfig
from ..event import EventBus, EventType
from ..paths import DREAM_DIR, MEMORY_DIR
from ..tool import ToolRegistry
from .agent import DreamAgent
from .cursor import DreamCursor
from .snapshot import SnapshotStore

if TYPE_CHECKING:
    from ..provider import Provider

logger = logging.getLogger(__name__)


@dataclass
class DreamResult:
    """Result of a dream cycle."""
    summaries_processed: int = 0
    edits_made: int = 0
    tool_calls_made: int = 0
    snapshot_id: str | None = None
    skipped: bool = False


class DreamManager:
    """Orchestrates the full dream cycle: analyze summaries -> edit memory files."""

    def __init__(
        self,
        config: DreamConfig,
        provider: "Provider",
        tools: ToolRegistry,
        event_bus: EventBus | None = None,
        dream_dir: Path | None = None,
        memory_dir: Path | None = None,
    ):
        self._config = config
        self._provider = provider
        self._tools = tools
        self._event_bus = event_bus
        self._dream_dir = dream_dir or DREAM_DIR
        self._memory_dir = memory_dir or MEMORY_DIR
        self._lock = asyncio.Lock()

        self._cursor = DreamCursor(self._dream_dir)
        self._snapshot = SnapshotStore(
            snapshot_dir=self._dream_dir / "snapshots",
            memory_dir=self._memory_dir,
            max_snapshots=config.max_snapshots,
        )
        self._agent = DreamAgent(provider, tools, max_tool_calls=config.max_tool_calls)

    def update_provider(self, provider: "Provider") -> None:
        self._provider = provider
        self._agent = DreamAgent(provider, self._tools, max_tool_calls=self._config.max_tool_calls)

    async def dream(self) -> DreamResult:
        async with self._lock:
            return await self._run_cycle()

    async def _run_cycle(self) -> DreamResult:
        summaries_dir = self._dream_dir / "summaries"
        new_files = self._cursor.get_new_summaries(summaries_dir)

        if not new_files:
            logger.debug("Dream: no new summaries to process")
            return DreamResult(skipped=True)

        if self._event_bus:
            self._event_bus.emit(EventType.DREAM_START, {
                "pending_summaries": len(new_files),
            })

        summaries = []
        summary_ids = []
        for f in new_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                summaries.append(data.get("summary", ""))
                summary_ids.append(data.get("id", f.stem))
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to read summary {f.name}: {e}")

        if not summaries:
            return DreamResult(skipped=True)

        if self._event_bus:
            self._event_bus.emit(EventType.DREAM_SUMMARY_AVAILABLE, {
                "summary_count": len(summaries),
                "summary_ids": summary_ids,
            })

        soul = self._read_memory("SOUL.md")
        user = self._read_memory("USER.md")
        memory = self._read_memory("MEMORY.md")

        snap_id = self._snapshot.snapshot(trigger="dream")

        agent_result = await self._agent.run(summaries, soul, user, memory)

        self._advance_cursor(summary_ids)

        result = DreamResult(
            summaries_processed=len(summaries),
            edits_made=agent_result.edits_made,
            tool_calls_made=agent_result.tool_calls_made,
            snapshot_id=snap_id,
        )

        if self._event_bus:
            self._event_bus.emit(EventType.DREAM_COMPLETE, {
                "summaries_processed": result.summaries_processed,
                "edits_made": result.edits_made,
                "tool_calls_made": result.tool_calls_made,
                "snapshot_id": result.snapshot_id,
            })

        logger.info(
            f"Dream cycle complete: {result.summaries_processed} summaries, "
            f"{result.edits_made} edits, {result.tool_calls_made} tool calls"
        )
        return result

    def _advance_cursor(self, summary_ids: list[str]) -> None:
        if summary_ids:
            self._cursor.advance(summary_ids[-1])

    def _read_memory(self, filename: str) -> str:
        path = self._memory_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def get_status(self) -> dict:
        cursor = self._cursor.load()
        summaries_dir = self._dream_dir / "summaries"
        new_count = len(self._cursor.get_new_summaries(summaries_dir))
        snapshots = self._snapshot.list()

        return {
            "enabled": self._config.enabled,
            "interval_seconds": self._config.interval_seconds,
            "last_summary_id": cursor.get("last_summary_id", ""),
            "total_processed": cursor.get("total_processed", 0),
            "pending_summaries": new_count,
            "snapshot_count": len(snapshots),
            "last_snapshot": snapshots[0] if snapshots else None,
        }

    def list_snapshots(self) -> list[dict]:
        return self._snapshot.list()

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        return self._snapshot.get(snapshot_id)

    def restore_snapshot(self, snapshot_id: str) -> bool:
        return self._snapshot.restore(snapshot_id)

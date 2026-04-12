"""Dream snapshot - version control for memory files"""

import json
import logging
from datetime import datetime
from pathlib import Path
from ...paths import DREAM_DIR, MEMORY_DIR

logger = logging.getLogger(__name__)

MEMORY_FILES = ("SOUL.md", "USER.md", "MEMORY.md")


class SnapshotStore:
    """Manages JSON snapshots of memory files for rollback."""

    _counter = 0

    def __init__(
        self,
        snapshot_dir: Path | None = None,
        memory_dir: Path | None = None,
        max_snapshots: int = 50,
    ):
        self._dir = snapshot_dir or (DREAM_DIR / "snapshots")
        self._memory_dir = memory_dir or MEMORY_DIR
        self._max = max_snapshots

    def _snapshot_id(self) -> str:
        SnapshotStore._counter += 1
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"snapshot_{ts}_{SnapshotStore._counter:04d}"

    def snapshot(self, trigger: str = "dream") -> str | None:
        """Save current memory files as a snapshot. Returns snapshot ID or None."""
        try:
            self._memory_dir.mkdir(parents=True, exist_ok=True)
            self._dir.mkdir(parents=True, exist_ok=True)

            files = {}
            for name in MEMORY_FILES:
                path = self._memory_dir / name
                files[name] = path.read_text(encoding="utf-8") if path.exists() else ""

            snap_id = self._snapshot_id()
            data = {
                "id": snap_id,
                "created_at": datetime.now().isoformat(),
                "trigger": trigger,
                "files": files,
            }

            path = self._dir / f"{snap_id}.json"
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(f"Created dream snapshot: {snap_id}")

            self.cleanup()
            return snap_id
        except Exception as e:
            logger.error(f"Failed to create snapshot: {e}")
            return None

    def restore(self, snapshot_id: str) -> bool:
        """Restore memory files from a snapshot. Returns True on success."""
        path = self._dir / f"{snapshot_id}.json"
        if not path.exists():
            logger.warning(f"Snapshot not found: {snapshot_id}")
            return False

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            files = data.get("files", {})
            self._memory_dir.mkdir(parents=True, exist_ok=True)

            for name in MEMORY_FILES:
                if name in files:
                    (self._memory_dir / name).write_text(
                        files[name], encoding="utf-8"
                    )
            logger.info(f"Restored snapshot: {snapshot_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore snapshot {snapshot_id}: {e}")
            return False

    def list(self) -> list[dict]:
        """List all snapshots, newest first."""
        if not self._dir.exists():
            return []

        results = []
        for p in sorted(self._dir.iterdir(), reverse=True):
            if p.is_file() and p.name.startswith("snapshot_") and p.suffix == ".json":
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    results.append({
                        "id": data.get("id", p.stem),
                        "created_at": data.get("created_at", ""),
                        "trigger": data.get("trigger", ""),
                    })
                except (json.JSONDecodeError, IOError):
                    pass
        return results

    def get(self, snapshot_id: str) -> dict | None:
        """Get full snapshot data including file contents."""
        path = self._dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return None

    def cleanup(self) -> None:
        """Remove oldest snapshots beyond max_snapshots."""
        if not self._dir.exists():
            return

        files = sorted(
            p for p in self._dir.iterdir()
            if p.is_file() and p.name.startswith("snapshot_") and p.suffix == ".json"
        )

        while len(files) > self._max:
            oldest = files.pop(0)
            oldest.unlink(missing_ok=True)
            logger.debug(f"Removed old snapshot: {oldest.name}")

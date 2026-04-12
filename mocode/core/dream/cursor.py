"""Dream cursor - tracks which summaries have been processed"""

import json
import logging
from pathlib import Path
from ...paths import DREAM_DIR

logger = logging.getLogger(__name__)

CURSOR_FILE = "cursor.json"


class DreamCursor:
    """Tracks last processed summary ID for the Dream pipeline."""

    def __init__(self, base_dir: Path | None = None):
        self._dir = base_dir or DREAM_DIR
        self._path = self._dir / CURSOR_FILE

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        """Load cursor state. Returns {"last_summary_id": "", "total_processed": 0} if missing."""
        if not self._path.exists():
            return {"last_summary_id": "", "total_processed": 0}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load dream cursor: {e}")
            return {"last_summary_id": "", "total_processed": 0}

    def save(self, last_summary_id: str, total_processed: int) -> None:
        """Persist cursor state."""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_summary_id": last_summary_id,
            "total_processed": total_processed,
        }
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def advance(self, summary_id: str) -> None:
        """Move cursor forward past the given summary."""
        state = self.load()
        state["last_summary_id"] = summary_id
        state["total_processed"] += 1
        self.save(summary_id, state["total_processed"])

    def get_new_summaries(self, summaries_dir: Path) -> list[Path]:
        """Return summary files after the cursor, sorted by name (lexicographic = chronological)."""
        if not summaries_dir.exists():
            return []

        all_files = sorted(
            p for p in summaries_dir.iterdir()
            if p.is_file() and p.suffix == ".json" and p.name.startswith("summary_")
        )

        state = self.load()
        last_id = state.get("last_summary_id", "")
        if not last_id:
            return all_files

        # Only return files whose name sorts after the cursor
        return [p for p in all_files if p.name > f"{last_id}.json"]

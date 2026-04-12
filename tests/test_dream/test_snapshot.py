"""Tests for SnapshotStore"""

import json
from pathlib import Path

from mocode.core.dream.snapshot import SnapshotStore, MEMORY_FILES


class TestSnapshotStore:
    """Test snapshot version management."""

    def _setup_memory(self, memory_dir: Path, files: dict[str, str] | None = None):
        """Create memory files for testing."""
        memory_dir.mkdir(parents=True, exist_ok=True)
        defaults = files or {
            "SOUL.md": "# Soul\nTest soul content",
            "USER.md": "# User\nTest user content",
            "MEMORY.md": "# Memory\nTest memory content",
        }
        for name, content in defaults.items():
            (memory_dir / name).write_text(content, encoding="utf-8")

    def test_snapshot_creates_file(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        snap_dir = tmp_path / "snapshots"
        self._setup_memory(memory_dir)

        store = SnapshotStore(snap_dir, memory_dir, max_snapshots=10)
        snap_id = store.snapshot(trigger="test")

        assert snap_id is not None
        assert (snap_dir / f"{snap_id}.json").exists()

    def test_snapshot_contains_files(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        snap_dir = tmp_path / "snapshots"
        self._setup_memory(memory_dir)

        store = SnapshotStore(snap_dir, memory_dir, max_snapshots=10)
        snap_id = store.snapshot()

        data = json.loads((snap_dir / f"{snap_id}.json").read_text(encoding="utf-8"))
        assert "SOUL.md" in data["files"]
        assert "Test soul content" in data["files"]["SOUL.md"]

    def test_restore_overwrites_files(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        snap_dir = tmp_path / "snapshots"
        self._setup_memory(memory_dir, {
            "SOUL.md": "Original",
            "USER.md": "Original user",
            "MEMORY.md": "Original memory",
        })

        store = SnapshotStore(snap_dir, memory_dir, max_snapshots=10)
        snap_id = store.snapshot()

        # Modify files
        (memory_dir / "SOUL.md").write_text("Modified", encoding="utf-8")

        # Restore
        assert store.restore(snap_id) is True
        assert (memory_dir / "SOUL.md").read_text(encoding="utf-8") == "Original"

    def test_restore_nonexistent(self, tmp_path: Path):
        store = SnapshotStore(tmp_path / "snapshots", tmp_path / "memory")
        assert store.restore("nonexistent_snapshot") is False

    def test_list_returns_newest_first(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        snap_dir = tmp_path / "snapshots"
        self._setup_memory(memory_dir)

        store = SnapshotStore(snap_dir, memory_dir, max_snapshots=10)

        ids = []
        # Create multiple snapshots with small delay-like naming
        for i in range(3):
            snap_id = store.snapshot(trigger=f"test_{i}")
            ids.append(snap_id)

        snapshots = store.list()
        assert len(snapshots) == 3
        # Newest first
        assert snapshots[0]["id"] == ids[-1]

    def test_list_empty(self, tmp_path: Path):
        store = SnapshotStore(tmp_path / "snapshots", tmp_path / "memory")
        assert store.list() == []

    def test_cleanup_removes_oldest(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        snap_dir = tmp_path / "snapshots"
        self._setup_memory(memory_dir)

        store = SnapshotStore(snap_dir, memory_dir, max_snapshots=3)

        ids = []
        for i in range(5):
            snap_id = store.snapshot(trigger=f"test_{i}")
            ids.append(snap_id)

        # Should only keep last 3
        snapshots = store.list()
        assert len(snapshots) == 3
        # The first two should be removed
        assert ids[0] not in [s["id"] for s in snapshots]
        assert ids[1] not in [s["id"] for s in snapshots]

    def test_get_returns_full_data(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        snap_dir = tmp_path / "snapshots"
        self._setup_memory(memory_dir)

        store = SnapshotStore(snap_dir, memory_dir, max_snapshots=10)
        snap_id = store.snapshot(trigger="test")

        data = store.get(snap_id)
        assert data is not None
        assert data["trigger"] == "test"
        assert "SOUL.md" in data["files"]

    def test_get_nonexistent(self, tmp_path: Path):
        store = SnapshotStore(tmp_path / "snapshots", tmp_path / "memory")
        assert store.get("nonexistent") is None

    def test_snapshot_empty_memory_dir(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        snap_dir = tmp_path / "snapshots"
        # Don't create memory files

        store = SnapshotStore(snap_dir, memory_dir, max_snapshots=10)
        snap_id = store.snapshot()
        assert snap_id is not None

        data = store.get(snap_id)
        # Files should be empty strings
        for name in MEMORY_FILES:
            assert data["files"][name] == ""

"""Tests for SnapshotStore"""

from pathlib import Path

import pytest

from mocode.dream.snapshot import SnapshotStore


@pytest.fixture
def snapshot_dir(tmp_path):
    return tmp_path / "snapshots"


@pytest.fixture
def memory_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    for name in ["SOUL.md", "USER.md", "MEMORY.md"]:
        (d / name).write_text(f"# {name}\nDefault content", encoding="utf-8")
    return d


@pytest.fixture
def store(snapshot_dir, memory_dir):
    return SnapshotStore(
        snapshot_dir=snapshot_dir,
        memory_dir=memory_dir,
        max_snapshots=3,
    )


def test_snapshot_creates_file(store, snapshot_dir):
    snap_id = store.snapshot(trigger="test")
    assert snap_id is not None
    assert (snapshot_dir / f"{snap_id}.json").exists()


def test_snapshot_preserves_content(store, memory_dir):
    (memory_dir / "SOUL.md").write_text("Important content", encoding="utf-8")
    snap_id = store.snapshot(trigger="test")

    # Modify
    (memory_dir / "SOUL.md").write_text("Modified", encoding="utf-8")

    # Restore
    assert store.restore(snap_id)
    assert (memory_dir / "SOUL.md").read_text(encoding="utf-8") == "Important content"


def test_list_snapshots(store):
    store.snapshot(trigger="a")
    store.snapshot(trigger="b")
    snapshots = store.list()
    assert len(snapshots) == 2


def test_cleanup_oldest(store):
    ids = []
    for i in range(5):
        ids.append(store.snapshot(trigger=f"test_{i}"))

    # max_snapshots=3, so only 3 should remain
    snapshots = store.list()
    assert len(snapshots) <= 3


def test_restore_nonexistent(store):
    assert not store.restore("nope")


def test_get_snapshot(store):
    snap_id = store.snapshot(trigger="test")
    data = store.get(snap_id)
    assert data is not None
    assert data["trigger"] == "test"

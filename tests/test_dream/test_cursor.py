"""Tests for DreamCursor"""

import json
from pathlib import Path

from mocode.core.dream.cursor import DreamCursor


class TestDreamCursor:
    """Test cursor tracking for Dream pipeline."""

    def test_load_empty(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        state = cursor.load()
        assert state == {"last_summary_id": "", "total_processed": 0}

    def test_save_and_load(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        cursor.save("summary_001", 5)

        state = cursor.load()
        assert state["last_summary_id"] == "summary_001"
        assert state["total_processed"] == 5

    def test_advance(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        cursor.advance("summary_001")
        cursor.advance("summary_002")

        state = cursor.load()
        assert state["last_summary_id"] == "summary_002"
        assert state["total_processed"] == 2

    def test_advance_increments(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        for i in range(10):
            cursor.advance(f"summary_{i:03d}")

        state = cursor.load()
        assert state["total_processed"] == 10
        assert state["last_summary_id"] == "summary_009"

    def test_get_new_summaries_empty_dir(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        result = cursor.get_new_summaries(summaries_dir)
        assert result == []

    def test_get_new_summaries_no_cursor(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        # Create some summary files
        for name in ["summary_001.json", "summary_002.json", "summary_003.json"]:
            (summaries_dir / name).write_text("{}", encoding="utf-8")

        result = cursor.get_new_summaries(summaries_dir)
        assert len(result) == 3

    def test_get_new_summaries_with_cursor(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        cursor.advance("summary_001")

        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        for name in ["summary_001.json", "summary_002.json", "summary_003.json"]:
            (summaries_dir / name).write_text("{}", encoding="utf-8")

        result = cursor.get_new_summaries(summaries_dir)
        assert len(result) == 2
        assert result[0].name == "summary_002.json"
        assert result[1].name == "summary_003.json"

    def test_get_new_summaries_sorted(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        # Create files in non-sorted order
        for name in ["summary_003.json", "summary_001.json", "summary_002.json"]:
            (summaries_dir / name).write_text("{}", encoding="utf-8")

        result = cursor.get_new_summaries(summaries_dir)
        names = [p.name for p in result]
        assert names == ["summary_001.json", "summary_002.json", "summary_003.json"]

    def test_corrupted_cursor_file(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        (tmp_path / "cursor.json").write_text("not json{{{", encoding="utf-8")

        state = cursor.load()
        assert state == {"last_summary_id": "", "total_processed": 0}

    def test_nonexistent_dir(self, tmp_path: Path):
        cursor = DreamCursor(tmp_path)
        summaries_dir = tmp_path / "nonexistent"
        result = cursor.get_new_summaries(summaries_dir)
        assert result == []

"""Tests for DreamCursor"""

import json
from pathlib import Path

import pytest

from mocode.dream.cursor import DreamCursor


@pytest.fixture
def dream_dir(tmp_path):
    return tmp_path / "dream"


@pytest.fixture
def cursor(dream_dir):
    return DreamCursor(dream_dir)


def test_load_default(cursor):
    state = cursor.load()
    assert state["last_summary_id"] == ""
    assert state["total_processed"] == 0


def test_advance(cursor, dream_dir):
    # Create summaries dir and a file
    summaries_dir = dream_dir / "summaries"
    summaries_dir.mkdir(parents=True)

    cursor.advance("summary_001")
    state = cursor.load()
    assert state["last_summary_id"] == "summary_001"
    assert state["total_processed"] == 1


def test_get_new_summaries(cursor, dream_dir):
    summaries_dir = dream_dir / "summaries"
    summaries_dir.mkdir(parents=True)

    # Create some summary files
    for i in range(1, 4):
        (summaries_dir / f"summary_{i:03d}.json").write_text(
            json.dumps({"id": f"summary_{i:03d}"}), encoding="utf-8"
        )

    # Before advancing, all are new
    new = cursor.get_new_summaries(summaries_dir)
    assert len(new) == 3

    # After advancing past 002, only 003 is new
    cursor.advance("summary_002")
    new = cursor.get_new_summaries(summaries_dir)
    assert len(new) == 1
    assert new[0].name == "summary_003.json"

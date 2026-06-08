"""Tests for tools/search.py — GrepTool, GlobTool, and shared utils."""

from __future__ import annotations

import pytest
from pathlib import Path

from mocode.tools.grep import GrepTool
from mocode.tools.glob import GlobTool
from mocode.tools.utils import (
    expand_context_indices,
    format_grep_content,
    format_grep_count,
    format_grep_files,
)
from mocode.core.virtualfs import VirtualFS


# ── ignore_case ──────────────────────────────────────────────


class TestGrepIgnoreCase:
    @pytest.mark.parametrize(
        "ignore_case,should_match",
        [(True, True), (False, False), ("true", True)],
    )
    def test_ignore_case(self, tmp_path: Path, ignore_case, should_match):
        (tmp_path / "a.py").write_text("class AgentLoop:\n    pass\n", encoding="utf-8")
        args = {
            "pattern": "agentloop",
            "path": str(tmp_path),
            "type": "py",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "ignore_case": ignore_case,
        }
        result = GrepTool().run(args)
        if should_match:
            assert "AgentLoop" in result
        else:
            assert "No matches" in result


# ── VFS separation ──────────────────────────────────────────


class TestGrepVfsSeparation:
    def test_real_path_excludes_vfs(self, tmp_path: Path):
        """Searching a real directory should NOT include VFS results."""
        (tmp_path / "real.py").write_text("hello world\n", encoding="utf-8")
        vfs = VirtualFS()
        vfs.add("virtual.py", "hello vfs\n")
        grep_tool = GrepTool(vfs=vfs)
        result = grep_tool.run({
            "pattern": "hello",
            "path": str(tmp_path),
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
        })
        assert "hello world" in result
        assert "vfs://" not in result


# ── single file search ───────────────────────────────────────


class TestGrepSingleFile:
    def test_single_file_content(self, tmp_path: Path):
        target = tmp_path / "main.py"
        target.write_text("import os\nimport sys\nprint('hello')\n", encoding="utf-8")
        args = {
            "pattern": "import",
            "path": str(target),
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
        }
        result = GrepTool().run(args)
        assert "import os" in result
        assert "import sys" in result

    def test_single_file_no_match(self, tmp_path: Path):
        target = tmp_path / "main.py"
        target.write_text("print('hello')\n", encoding="utf-8")
        args = {
            "pattern": "import",
            "path": str(target),
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
        }
        result = GrepTool().run(args)
        assert "No matches" in result


# ── tool schema ──────────────────────────────────────────────


class TestGrepToolSchema:
    def test_schema_has_ignore_case(self):
        tool = GrepTool()
        schema = tool.to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "ignore_case" in props
        assert props["ignore_case"]["type"] == "boolean"


# ── VFS subdirectory filtering ─────────────────────────────


class TestGlobVfsSubdir:
    def test_glob_vfs_subdir(self):
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflow/b.md", "B")
        vfs.add("amesim/c.md", "C")
        tool = GlobTool(vfs=vfs)
        result = tool.run({"pattern": "*.md", "path": "vfs://workflow"})
        assert "a.md" in result
        assert "b.md" in result
        assert "c.md" not in result


class TestGrepVfsSubdir:
    def test_grep_vfs_subdir_content(self):
        vfs = VirtualFS()
        vfs.add("workflow/guide.md", "run the flow")
        vfs.add("other/doc.md", "flow chart")
        tool = GrepTool(vfs=vfs)
        result = tool.run({
            "pattern": "flow",
            "path": "vfs://workflow",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
        })
        assert "guide.md" in result
        assert "doc.md" not in result


# ── shared utils ────────────────────────────────────────────


class TestExpandContextIndices:
    def test_no_context(self):
        assert expand_context_indices([2, 5], 10, 0) == [2, 5]

    def test_context_expands(self):
        result = expand_context_indices([3], 10, 1)
        assert result == [2, 3, 4]


class TestFormatGrepFiles:
    def test_with_files(self):
        result = format_grep_files(["a.py", "b.py"], "pattern")
        assert "Found 2 file(s)" in result
        assert "a.py" in result
        assert "b.py" in result


class TestFormatGrepCount:
    def test_with_entries(self):
        result = format_grep_count(["a.py:3", "b.py:1"], "pattern")
        assert "a.py:3" in result
        assert "b.py:1" in result


class TestFormatGrepContent:
    def test_returns_empty_when_not_full(self):
        hits: list[str] = []
        result = format_grep_content(
            ["line1", "match", "line3"],
            [1], [0, 1, 2],
            "file.py", "match", 100, hits,
        )
        assert result == ""  # not at max
        assert len(hits) == 3

    def test_returns_result_at_max(self):
        hits: list[str] = []
        result = format_grep_content(
            ["match1", "match2"],
            [0, 1], [0, 1],
            "file.py", "match", 2, hits,
        )
        assert "Showing" in result
        assert "matches" in result

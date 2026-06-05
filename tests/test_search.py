"""Tests for tools/search.py — GrepTool, GlobTool, and shared utils."""

from __future__ import annotations

import pytest
from pathlib import Path

from mocode.tools.search import _grep, GrepTool, GlobTool
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
        result = _grep(args)
        if should_match:
            assert "AgentLoop" in result
        else:
            assert "No matches" in result

    def test_ignore_case_with_vfs(self):
        vfs = VirtualFS()
        vfs.add("ref.md", "AgentLoop is the core engine")
        grep_tool = GrepTool(vfs=vfs)
        result = grep_tool.run({
            "pattern": "agentloop",
            "path": "vfs://",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
            "ignore_case": True,
        })
        assert "AgentLoop" in result


# ── single file search ───────────────────────────────────────


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

    def test_vfs_path_excludes_real(self, tmp_path: Path):
        """Searching VFS should NOT include real filesystem results."""
        (tmp_path / "real.py").write_text("hello world\n", encoding="utf-8")
        vfs = VirtualFS()
        vfs.add("virtual.py", "hello vfs\n")
        grep_tool = GrepTool(vfs=vfs)
        result = grep_tool.run({
            "pattern": "hello",
            "path": "vfs://",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
        })
        assert "hello vfs" in result
        assert str(tmp_path) not in result


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
        result = _grep(args)
        assert "import os" in result
        assert "import sys" in result

    def test_single_file_count(self, tmp_path: Path):
        target = tmp_path / "main.py"
        target.write_text("import os\nimport sys\nprint('hello')\n", encoding="utf-8")
        args = {
            "pattern": "import",
            "path": str(target),
            "output_mode": "count",
            "limit": 100,
            "context": 0,
            "type": "",
        }
        result = _grep(args)
        assert ":2" in result

    def test_single_file_files_mode(self, tmp_path: Path):
        target = tmp_path / "main.py"
        target.write_text("import os\n", encoding="utf-8")
        args = {
            "pattern": "import",
            "path": str(target),
            "output_mode": "files",
            "limit": 100,
            "context": 0,
            "type": "",
        }
        result = _grep(args)
        assert "Found 1 file" in result
        assert "main.py" in result

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
        result = _grep(args)
        assert "No matches" in result

    def test_single_file_with_context(self, tmp_path: Path):
        target = tmp_path / "main.py"
        target.write_text("line1\nline2\nmatch_here\nline4\nline5\n", encoding="utf-8")
        args = {
            "pattern": "match_here",
            "path": str(target),
            "output_mode": "content",
            "limit": 100,
            "context": 1,
            "type": "",
        }
        result = _grep(args)
        assert "match_here" in result
        assert "line2" in result
        assert "line4" in result

    def test_single_file_ignore_case(self, tmp_path: Path):
        target = tmp_path / "main.py"
        target.write_text("Error: something failed\n", encoding="utf-8")
        args = {
            "pattern": "error",
            "path": str(target),
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
            "ignore_case": True,
        }
        result = _grep(args)
        assert "Error" in result


# ── tool schema ──────────────────────────────────────────────


class TestGrepToolSchema:
    def test_schema_has_ignore_case(self):
        tool = GrepTool()
        schema = tool.to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "ignore_case" in props
        assert props["ignore_case"]["type"] == "boolean"

    def test_ignore_case_not_required(self):
        tool = GrepTool()
        schema = tool.to_schema()
        required = schema["function"]["parameters"]["required"]
        assert "ignore_case" not in required


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

    def test_glob_vfs_root(self):
        """path='vfs://' should search all VFS files."""
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("other/b.md", "B")
        tool = GlobTool(vfs=vfs)
        result = tool.run({"pattern": "*.md", "path": "vfs://"})
        assert "a.md" in result
        assert "b.md" in result


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

    def test_grep_vfs_subdir_count(self):
        vfs = VirtualFS()
        vfs.add("workflow/guide.md", "flow\nflow")
        vfs.add("other/doc.md", "flow")
        tool = GrepTool(vfs=vfs)
        result = tool.run({
            "pattern": "flow",
            "path": "vfs://workflow",
            "output_mode": "count",
            "limit": 100,
            "context": 0,
            "type": "",
        })
        assert "guide.md:2" in result
        assert "other" not in result

    def test_grep_vfs_root(self):
        """path='vfs://' should search all VFS files."""
        vfs = VirtualFS()
        vfs.add("a.md", "hello")
        tool = GrepTool(vfs=vfs)
        result = tool.run({
            "pattern": "hello",
            "path": "vfs://",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "type": "",
        })
        assert "a.md" in result


# ── shared utils ────────────────────────────────────────────


class TestExpandContextIndices:
    def test_no_context(self):
        assert expand_context_indices([2, 5], 10, 0) == [2, 5]

    def test_context_expands(self):
        result = expand_context_indices([3], 10, 1)
        assert result == [2, 3, 4]

    def test_context_clamped(self):
        result = expand_context_indices([0], 5, 2)
        assert result == [0, 1, 2]

    def test_context_merges_overlapping(self):
        result = expand_context_indices([2, 4], 10, 1)
        assert result == [1, 2, 3, 4, 5]


class TestFormatGrepFiles:
    def test_empty(self):
        assert "No files" in format_grep_files([], "pattern")

    def test_with_files(self):
        result = format_grep_files(["a.py", "b.py"], "pattern")
        assert "Found 2 file(s)" in result
        assert "a.py" in result
        assert "b.py" in result


class TestFormatGrepCount:
    def test_empty(self):
        assert "No matches" in format_grep_count([], "pattern")

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

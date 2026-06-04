"""Tests for tools/search.py — GrepTool and GlobTool."""

from __future__ import annotations

import pytest
from pathlib import Path

from mocode.tools.search import _grep, GrepTool, GlobTool
from mocode.core.virtualfs import VirtualFS


# ── ignore_case ──────────────────────────────────────────────


class TestGrepIgnoreCase:
    def test_ignore_case_true(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("class AgentLoop:\n    pass\n", encoding="utf-8")
        args = {
            "pattern": "agentloop",
            "path": str(tmp_path),
            "type": "py",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "ignore_case": True,
        }
        result = _grep(args)
        assert "AgentLoop" in result

    def test_ignore_case_false_by_default(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("class AgentLoop:\n    pass\n", encoding="utf-8")
        args = {
            "pattern": "agentloop",
            "path": str(tmp_path),
            "type": "py",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
        }
        result = _grep(args)
        assert "No matches" in result

    def test_ignore_case_explicit_false(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("class AgentLoop:\n    pass\n", encoding="utf-8")
        args = {
            "pattern": "agentloop",
            "path": str(tmp_path),
            "type": "py",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "ignore_case": False,
        }
        result = _grep(args)
        assert "No matches" in result

    def test_ignore_case_string_true(self, tmp_path: Path):
        """Handle string 'true' from LLM tool calls."""
        (tmp_path / "a.py").write_text("Error: something failed\n", encoding="utf-8")
        args = {
            "pattern": "error",
            "path": str(tmp_path),
            "type": "py",
            "output_mode": "content",
            "limit": 100,
            "context": 0,
            "ignore_case": "true",
        }
        result = _grep(args)
        assert "Error" in result

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

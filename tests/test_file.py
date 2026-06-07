"""Tests for tools/file.py — ReadTool directory graceful degradation."""

from __future__ import annotations

from pathlib import Path

from mocode.tools.file import ReadTool


class TestReadDirectory:
    def test_read_directory_lists_contents(self, tmp_path: Path):
        """read() on a directory should list its contents, not error."""
        (tmp_path / "subdir").mkdir()
        (tmp_path / "hello.py").write_text("print('hi')", encoding="utf-8")
        tool = ReadTool()
        result = tool.run({"path": str(tmp_path)})
        assert "subdir/" in result
        assert "hello.py" in result

    def test_read_directory_excludes_ignore_dirs(self, tmp_path: Path):
        """read() on a directory should skip IGNORE_DIRS."""
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "real.py").write_text("x", encoding="utf-8")
        tool = ReadTool()
        result = tool.run({"path": str(tmp_path)})
        assert "__pycache__" not in result
        assert "real.py" in result

    def test_read_directory_empty(self, tmp_path: Path):
        """read() on an empty directory should show 0 counts."""
        tool = ReadTool()
        result = tool.run({"path": str(tmp_path)})
        assert "0 directories" in result
        assert "0 files" in result

    def test_read_directory_shows_size(self, tmp_path: Path):
        """read() on a directory should show file sizes."""
        (tmp_path / "big.py").write_text("x" * 2048, encoding="utf-8")
        tool = ReadTool()
        result = tool.run({"path": str(tmp_path)})
        assert "2.0 KB" in result

    def test_read_directory_hint_message(self, tmp_path: Path):
        """read() on a directory should include hint about using glob."""
        tool = ReadTool()
        result = tool.run({"path": str(tmp_path)})
        assert "directory" in result.lower()
        assert "glob" in result.lower()

    def test_read_directory_format_header(self, tmp_path: Path):
        """read() on a directory should use [bracket] header like glob/grep."""
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        tool = ReadTool()
        result = tool.run({"path": str(tmp_path)})
        assert result.startswith("[")
        assert "directories" in result
        assert "files" in result

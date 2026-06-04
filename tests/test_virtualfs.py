"""Tests for core/virtualfs.py."""

import pytest
from pathlib import Path

from mocode.core.virtualfs import VirtualFS


class TestVirtualFSBasic:
    def test_add_and_get(self):
        vfs = VirtualFS()
        vfs.add("vfs://demo/hello.md", "Hello world")
        assert vfs.get("vfs://demo/hello.md") == "Hello world"

    def test_add_auto_prefix(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "content")
        assert vfs.get("vfs://demo/hello.md") == "content"

    def test_get_auto_prefix(self):
        vfs = VirtualFS()
        vfs.add("vfs://demo/hello.md", "content")
        assert vfs.get("demo/hello.md") == "content"

    def test_get_missing_returns_none(self):
        vfs = VirtualFS()
        assert vfs.get("vfs://nope.md") is None

    def test_exists_true(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "content")
        assert vfs.exists("vfs://demo/hello.md") is True
        assert vfs.exists("demo/hello.md") is True

    def test_exists_false(self):
        vfs = VirtualFS()
        assert vfs.exists("vfs://nope.md") is False

    def test_add_overwrites(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "v1")
        vfs.add("vfs://demo/hello.md", "v2")
        assert vfs.get("vfs://demo/hello.md") == "v2"


class TestRemove:
    def test_remove_existing(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "content")
        assert vfs.remove("demo/hello.md") is True
        assert vfs.exists("demo/hello.md") is False

    def test_remove_missing(self):
        vfs = VirtualFS()
        assert vfs.remove("vfs://nope.md") is False


class TestItems:
    def test_empty(self):
        vfs = VirtualFS()
        assert vfs.items() == []

    def test_returns_pairs(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        vfs.add("b.md", "B")
        pairs = dict(vfs.items())
        assert pairs["vfs://a.md"] == "A"
        assert pairs["vfs://b.md"] == "B"


class TestGlob:
    def test_glob_basic(self):
        vfs = VirtualFS()
        vfs.add("skill/a.md", "A")
        vfs.add("skill/b.md", "B")
        vfs.add("other/c.md", "C")
        assert vfs.glob("vfs://skill/*.md") == ["vfs://skill/a.md", "vfs://skill/b.md"]

    def test_glob_subdir(self):
        vfs = VirtualFS()
        vfs.add("skill/sub/deep.md", "deep")
        vfs.add("skill/top.md", "top")
        # fnmatch doesn't support **; use * which matches across /
        result = vfs.glob("vfs://skill/*.md")
        assert "vfs://skill/top.md" in result

    def test_glob_no_match(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        assert vfs.glob("vfs://*.py") == []

    def test_glob_strips_prefix_correctly(self):
        """Regression: lstrip('vfs://') was stripping individual chars."""
        vfs = VirtualFS()
        vfs.add("vfs://v-file.md", "content")
        assert vfs.glob("vfs://v-file.md") == ["vfs://v-file.md"]
        assert vfs.glob("vfs://*.md") == ["vfs://v-file.md"]


class TestGrep:
    def test_grep_content_basic(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello world\nfoo bar")
        result = vfs.grep("hello")
        assert "vfs://a.md" in result
        assert "hello world" in result

    def test_grep_files_mode(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello world")
        vfs.add("b.md", "no match")
        result = vfs.grep("hello", output_mode="files")
        assert "vfs://a.md" in result
        assert "vfs://b.md" not in result

    def test_grep_count_mode(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello\nhello\nworld")
        result = vfs.grep("hello", output_mode="count")
        assert "vfs://a.md:2" in result

    def test_grep_type_filter(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello")
        vfs.add("b.py", "hello")
        result = vfs.grep("hello", type_filter={".py"}, output_mode="files")
        assert "vfs://b.py" in result
        assert "vfs://a.md" not in result

    def test_grep_no_match(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello")
        result = vfs.grep("zzz")
        assert "No matches" in result

    def test_grep_context_lines(self):
        vfs = VirtualFS()
        vfs.add("a.md", "line1\nline2\nMATCH\nline4\nline5")
        result = vfs.grep("MATCH", context_lines=1, output_mode="content")
        assert "line2" in result
        assert "line4" in result


class TestMountDirectory:
    def test_mount_basic(self, tmp_path: Path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "ref.md").write_text("reference content", encoding="utf-8")
        (skill_dir / "SKILL.md").write_text("skill body", encoding="utf-8")
        (skill_dir / "__init__.py").write_text("", encoding="utf-8")

        vfs = VirtualFS()
        mounted = vfs.mount_directory(skill_dir, "my-skill")

        assert "vfs://my-skill/ref.md" in mounted
        assert mounted["vfs://my-skill/ref.md"] == "reference content"
        # SKILL.md and __init__.py are skipped
        assert "vfs://my-skill/SKILL.md" not in mounted
        assert "vfs://my-skill/__init__.py" not in mounted

    def test_mount_nested(self, tmp_path: Path):
        skill_dir = tmp_path / "nested"
        sub = skill_dir / "sub"
        sub.mkdir(parents=True)
        (sub / "deep.md").write_text("deep content", encoding="utf-8")

        vfs = VirtualFS()
        mounted = vfs.mount_directory(skill_dir, "nested")
        assert "vfs://nested/sub/deep.md" in mounted
        # Also verify it's accessible via get()
        assert vfs.get("vfs://nested/sub/deep.md") == "deep content"

    def test_mount_skips_pycache(self, tmp_path: Path):
        skill_dir = tmp_path / "cache-test"
        cache = skill_dir / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "mod.cpython-312.pyc").write_bytes(b"\x00")
        (skill_dir / "good.md").write_text("ok", encoding="utf-8")

        vfs = VirtualFS()
        mounted = vfs.mount_directory(skill_dir, "cache-test")
        assert len(mounted) == 1
        assert "vfs://cache-test/good.md" in mounted

    def test_mount_nonexistent_dir(self, tmp_path: Path):
        vfs = VirtualFS()
        mounted = vfs.mount_directory(tmp_path / "nope", "nope")
        assert mounted == {}

    def test_mount_custom_skip(self, tmp_path: Path):
        skill_dir = tmp_path / "custom"
        skill_dir.mkdir()
        (skill_dir / "keep.md").write_text("keep", encoding="utf-8")
        (skill_dir / "skip.md").write_text("skip", encoding="utf-8")

        vfs = VirtualFS()
        mounted = vfs.mount_directory(skill_dir, "custom", skip={"skip.md"})
        assert "vfs://custom/keep.md" in mounted
        assert "vfs://custom/skip.md" not in mounted

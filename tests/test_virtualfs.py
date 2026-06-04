"""Tests for core/virtualfs.py."""

import pytest
from pathlib import Path

from mocode.core.virtualfs import VirtualFS


class TestVirtualFSBasic:
    def test_add_and_get(self):
        vfs = VirtualFS()
        vfs.add("vfs://demo/hello.md", "Hello world")
        assert vfs.get("vfs://demo/hello.md") == "Hello world"

    def test_get_missing_returns_none(self):
        vfs = VirtualFS()
        assert vfs.get("vfs://nope.md") is None

    def test_exists_true(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "content")
        assert vfs.exists("vfs://demo/hello.md") is True

    def test_exists_false(self):
        vfs = VirtualFS()
        assert vfs.exists("vfs://nope.md") is False


class TestRemove:
    def test_remove_existing(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "content")
        assert vfs.remove("demo/hello.md") is True
        assert vfs.exists("demo/hello.md") is False


class TestGlob:
    def test_glob_basic(self):
        vfs = VirtualFS()
        vfs.add("skill/a.md", "A")
        vfs.add("skill/b.md", "B")
        vfs.add("other/c.md", "C")
        assert vfs.glob("vfs://skill/*.md") == ["vfs://skill/a.md", "vfs://skill/b.md"]

    def test_glob_no_match(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        assert vfs.glob("vfs://*.py") == []


class TestGrep:
    def test_grep_content_basic(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello world\nfoo bar")
        result = vfs.grep("hello")
        assert "vfs://a.md" in result
        assert "hello world" in result

    def test_grep_no_match(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello")
        result = vfs.grep("zzz")
        assert "No matches" in result

    def test_grep_ignore_case(self):
        vfs = VirtualFS()
        vfs.add("a.md", "Hello World\nfoo bar\nHELLO again")
        result = vfs.grep("hello", ignore_case=True)
        assert "Hello World" in result
        assert "HELLO again" in result

    def test_grep_ignore_case_false_by_default(self):
        vfs = VirtualFS()
        vfs.add("a.md", "Hello World\nfoo bar")
        result = vfs.grep("hello")
        assert "No matches" in result

    def test_grep_ignore_case_with_pattern(self):
        """Pre-compiled pattern with IGNORECASE should also work."""
        import re
        vfs = VirtualFS()
        vfs.add("a.md", "AgentLoop\nagentloop")
        pattern = re.compile("agentloop", re.IGNORECASE)
        result = vfs.grep(pattern)
        assert "AgentLoop" in result
        assert "agentloop" in result


class TestMountDirectory:
    def test_mount_basic(self, tmp_path: Path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "ref.md").write_text("reference content", encoding="utf-8")
        (skill_dir / "SKILL.md").write_text("skill body", encoding="utf-8")

        vfs = VirtualFS()
        mounted = vfs.mount_directory(skill_dir, "my-skill")

        assert "vfs://my-skill/ref.md" in mounted
        assert mounted["vfs://my-skill/ref.md"] == "reference content"
        # SKILL.md is skipped
        assert "vfs://my-skill/SKILL.md" not in mounted

    def test_mount_nonexistent_dir(self, tmp_path: Path):
        vfs = VirtualFS()
        mounted = vfs.mount_directory(tmp_path / "nope", "nope")
        assert mounted == {}

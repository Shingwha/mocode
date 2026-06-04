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


class TestGlobPath:
    """Tests for VirtualFS.glob with path (subdirectory) filtering."""

    def test_glob_subdir_basic(self):
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflow/b.md", "B")
        vfs.add("other/c.md", "C")
        result = vfs.glob("*.md", path="vfs://workflow")
        assert result == ["vfs://workflow/a.md", "vfs://workflow/b.md"]

    def test_glob_subdir_root_returns_all(self):
        """path='vfs://' should search everything."""
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        vfs.add("b.py", "B")
        result = vfs.glob("vfs://*", path="vfs://")
        assert result == ["vfs://a.md", "vfs://b.py"]

    def test_glob_subdir_no_match(self):
        vfs = VirtualFS()
        vfs.add("other/a.md", "A")
        assert vfs.glob("*.md", path="vfs://workflow") == []

    def test_glob_subdir_deep_nested(self):
        """Multi-level nested directory filtering."""
        vfs = VirtualFS()
        vfs.add("workflow/reference/example/test.md", "deep")
        vfs.add("workflow/reference/guide.md", "guide")
        vfs.add("workflow/overview.md", "overview")
        result = vfs.glob("*.md", path="vfs://workflow/reference/example")
        assert result == ["vfs://workflow/reference/example/test.md"]

    def test_glob_subdir_no_prefix_leak(self):
        """Ensure 'workflow/' does not match 'workflowish/'."""
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflowish/b.md", "B")
        result = vfs.glob("*.md", path="vfs://workflow")
        assert result == ["vfs://workflow/a.md"]

    def test_glob_no_path_backward_compat(self):
        """Omitting path should search all files (backward compatible)."""
        vfs = VirtualFS()
        vfs.add("skill/a.md", "A")
        vfs.add("other/b.md", "B")
        result = vfs.glob("vfs://*/*.md")
        assert result == ["vfs://other/b.md", "vfs://skill/a.md"]


class TestGrepPath:
    """Tests for VirtualFS.grep with path (subdirectory) filtering."""

    def test_grep_subdir_content(self):
        vfs = VirtualFS()
        vfs.add("workflow/guide.md", "run the workflow")
        vfs.add("other/readme.md", "workflow is cool")
        result = vfs.grep("workflow", path="vfs://workflow")
        assert "vfs://workflow/guide.md" in result
        assert "vfs://other/readme.md" not in result

    def test_grep_root_path_returns_all(self):
        vfs = VirtualFS()
        vfs.add("a.md", "hello")
        result = vfs.grep("hello", path="vfs://")
        assert "vfs://a.md" in result

    def test_grep_subdir_files_mode(self):
        vfs = VirtualFS()
        vfs.add("workflow/guide.md", "run the flow")
        vfs.add("other/doc.md", "flow chart")
        result = vfs.grep("flow", output_mode="files", path="vfs://workflow")
        assert "vfs://workflow/guide.md" in result
        assert "vfs://other/doc.md" not in result

    def test_grep_subdir_count_mode(self):
        vfs = VirtualFS()
        vfs.add("workflow/guide.md", "flow\nflow\nflow")
        vfs.add("other/doc.md", "flow")
        result = vfs.grep("flow", output_mode="count", path="vfs://workflow")
        assert "vfs://workflow/guide.md:3" in result
        assert "other" not in result

    def test_grep_subdir_no_match(self):
        vfs = VirtualFS()
        vfs.add("other/readme.md", "workflow is cool")
        result = vfs.grep("workflow", path="vfs://workflow")
        assert "No matches" in result

    def test_grep_no_path_backward_compat(self):
        """Omitting path should search all files (backward compatible)."""
        vfs = VirtualFS()
        vfs.add("a.md", "hello")
        vfs.add("b.md", "hello")
        result = vfs.grep("hello")
        assert "vfs://a.md" in result
        assert "vfs://b.md" in result

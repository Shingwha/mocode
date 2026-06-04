"""Tests for core/virtualfs.py."""

import pytest

from mocode.core.virtualfs import VirtualFS


class TestVirtualFS:
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

    def test_glob_match(self):
        vfs = VirtualFS()
        vfs.add("vfs://workflow/yaml-reference.md", "yaml")
        vfs.add("vfs://workflow/cli-reference.md", "cli")
        vfs.add("vfs://other/readme.md", "readme")

        matches = vfs.glob("vfs://workflow/*.md")
        assert len(matches) == 2
        assert "vfs://workflow/cli-reference.md" in matches
        assert "vfs://workflow/yaml-reference.md" in matches

    def test_glob_without_prefix(self):
        vfs = VirtualFS()
        vfs.add("vfs://workflow/yaml.md", "yaml")

        matches = vfs.glob("workflow/*.md")
        assert matches == ["vfs://workflow/yaml.md"]

    def test_glob_no_match(self):
        vfs = VirtualFS()
        vfs.add("vfs://workflow/yaml.md", "yaml")
        assert vfs.glob("vfs://other/*.md") == []

    def test_grep_finds_match(self):
        vfs = VirtualFS()
        vfs.add("vfs://workflow/yaml.md", "line one\nmatch here\nline three")

        hits = vfs.grep("match")
        assert len(hits) == 1
        assert hits[0] == ("vfs://workflow/yaml.md", 2, "match here")

    def test_grep_regex(self):
        vfs = VirtualFS()
        vfs.add("vfs://a.md", "foo 123 bar")
        vfs.add("vfs://b.md", "no digits here")

        hits = vfs.grep(r"\d+")
        assert len(hits) == 1
        assert hits[0][0] == "vfs://a.md"

    def test_grep_no_match(self):
        vfs = VirtualFS()
        vfs.add("vfs://a.md", "nothing to see")
        assert vfs.grep("xyz") == []

    def test_grep_sorted_by_path(self):
        vfs = VirtualFS()
        vfs.add("vfs://b.md", "match")
        vfs.add("vfs://a.md", "match")

        hits = vfs.grep("match")
        assert [h[0] for h in hits] == ["vfs://a.md", "vfs://b.md"]

    def test_add_overwrites(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "v1")
        vfs.add("vfs://demo/hello.md", "v2")
        assert vfs.get("vfs://demo/hello.md") == "v2"

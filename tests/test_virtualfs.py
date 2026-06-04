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


class TestList:
    def test_list_returns_all(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        vfs.add("b.md", "B")
        result = vfs.list()
        assert set(result) == {"vfs://a.md", "vfs://b.md"}


class TestItems:
    def test_items_returns_pairs(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        result = vfs.items()
        assert ("vfs://a.md", "A") in result


class TestIterFiles:
    def test_iter_files_all(self):
        vfs = VirtualFS()
        vfs.add("skill/a.md", "A")
        vfs.add("other/b.md", "B")
        result = list(vfs.iter_files())
        assert set(result) == {"vfs://skill/a.md", "vfs://other/b.md"}

    def test_iter_files_filtered(self):
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflow/b.md", "B")
        vfs.add("other/c.md", "C")
        result = list(vfs.iter_files("vfs://workflow"))
        assert set(result) == {"vfs://workflow/a.md", "vfs://workflow/b.md"}

    def test_iter_files_root(self):
        """path='vfs://' should return all files."""
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        vfs.add("b.md", "B")
        result = list(vfs.iter_files("vfs://"))
        assert set(result) == {"vfs://a.md", "vfs://b.md"}

    def test_iter_files_no_prefix_leak(self):
        """Ensure 'workflow/' does not match 'workflowish/'."""
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflowish/b.md", "B")
        result = list(vfs.iter_files("vfs://workflow"))
        assert result == ["vfs://workflow/a.md"]

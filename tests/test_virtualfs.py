"""Tests for core/virtualfs.py."""

from __future__ import annotations

import pytest
from pathlib import Path

from mocode.core.virtualfs import VirtualFS


class TestVirtualFS:
    def test_add_and_get(self):
        vfs = VirtualFS()
        vfs.add("vfs://demo/hello.md", "Hello world")
        assert vfs.get("vfs://demo/hello.md") == "Hello world"

    def test_get_missing_returns_none(self):
        vfs = VirtualFS()
        assert vfs.get("vfs://nope.md") is None

    def test_exists(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "content")
        assert vfs.exists("vfs://demo/hello.md") is True
        assert vfs.exists("vfs://nope.md") is False

    def test_remove_existing(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "content")
        assert vfs.remove("demo/hello.md") is True
        assert vfs.exists("demo/hello.md") is False

    def test_list_returns_all(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        vfs.add("b.md", "B")
        result = vfs.list()
        assert set(result) == {"vfs://a.md", "vfs://b.md"}


class TestVirtualFSIterFiles:
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

    def test_iter_files_no_prefix_leak(self):
        """Ensure 'workflow/' does not match 'workflowish/'."""
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflowish/b.md", "B")
        result = list(vfs.iter_files("vfs://workflow"))
        assert result == ["vfs://workflow/a.md"]

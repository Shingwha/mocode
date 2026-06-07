"""Tests for core/virtualfs.py."""

from __future__ import annotations

import pytest
from pathlib import Path

from mocode.core.virtualfs import VirtualFS


class TestVirtualFS:
    def test_add_and_get(self):
        vfs = VirtualFS()
        vfs.add("vfs://demo/hello.md", "Hello world")
        assert vfs["vfs://demo/hello.md"] == "Hello world"

    def test_get_missing_raises_key_error(self):
        vfs = VirtualFS()
        with pytest.raises(KeyError):
            _ = vfs["vfs://nope.md"]

    def test_get_missing_via_mapping_get(self):
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
        result = list(vfs)
        assert set(result) == {"vfs://a.md", "vfs://b.md"}


class TestVirtualFSMapping:
    def test_len(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        vfs.add("b.md", "B")
        assert len(vfs) == 2

    def test_contains(self):
        vfs = VirtualFS()
        vfs.add("skill/readme.md", "content")
        assert "vfs://skill/readme.md" in vfs
        assert "vfs://missing.md" not in vfs

    def test_iteration(self):
        vfs = VirtualFS()
        vfs.add("x.md", "X")
        vfs.add("y.md", "Y")
        keys = set(vfs)
        assert keys == {"vfs://x.md", "vfs://y.md"}

    def test_dict_conversion(self):
        vfs = VirtualFS()
        vfs.add("a.md", "A")
        d = dict(vfs)
        assert d == {"vfs://a.md": "A"}

    def test_prefix_filtering(self):
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflow/b.md", "B")
        vfs.add("other/c.md", "C")
        prefix = "vfs://workflow/"
        result = [p for p in vfs if p.startswith(prefix)]
        assert set(result) == {"vfs://workflow/a.md", "vfs://workflow/b.md"}

    def test_prefix_no_leak(self):
        """Ensure 'workflow/' does not match 'workflowish/'."""
        vfs = VirtualFS()
        vfs.add("workflow/a.md", "A")
        vfs.add("workflowish/b.md", "B")
        prefix = "vfs://workflow/"
        result = [p for p in vfs if p.startswith(prefix)]
        assert result == ["vfs://workflow/a.md"]

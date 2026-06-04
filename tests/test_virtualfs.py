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

    def test_add_overwrites(self):
        vfs = VirtualFS()
        vfs.add("demo/hello.md", "v1")
        vfs.add("vfs://demo/hello.md", "v2")
        assert vfs.get("vfs://demo/hello.md") == "v2"

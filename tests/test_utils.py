"""Utility function tests"""

from mocode.tools.utils import truncate_result
from mocode.core.utils import preview_result


class TestTruncateResult:
    def test_within_limit(self):
        result = truncate_result("hello", limit=100)
        assert result == "hello"

    def test_exceeds_limit(self):
        result = truncate_result("a" * 200, limit=100)
        assert len(result) > 100
        assert result.startswith("a" * 100)
        assert "truncated" in result

    def test_zero_limit(self):
        long_text = "a" * 1000
        result = truncate_result(long_text, limit=0)
        assert result == long_text

    def test_exact_limit(self):
        text = "a" * 100
        result = truncate_result(text, limit=100)
        assert result == text

    def test_custom_message(self):
        result = truncate_result("a" * 200, limit=100, truncate_message="[CUT]")
        assert result.endswith("[CUT]")


class TestPreviewResult:
    def test_short(self):
        assert preview_result("hello") == "hello"

    def test_long(self):
        result = preview_result("a" * 100, max_length=50)
        assert result.endswith("...")
        assert len(result) < 100

    def test_multiline(self):
        result = preview_result("line1\nline2\nline3")
        assert "+2 lines" in result

    def test_single_line_truncated(self):
        result = preview_result("a" * 100, max_length=50)
        assert "..." in result

    def test_empty(self):
        result = preview_result("")
        assert result == ""

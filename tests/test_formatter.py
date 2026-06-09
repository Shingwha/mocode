"""Tests for formatter — usage parts, suffixes, summaries, and skip styles."""

from __future__ import annotations

from mocode.app.cli.formatter import (
    _usage_suffix,
    build_usage_parts,
    detailed_summarize,
    summarize,
)
from mocode.app.workflow.models import NodeResult


# ── build_usage_parts ─────────────────────────────────────


class TestBuildUsageParts:
    def test_all_zero(self):
        assert build_usage_parts(0, 0, 0) == []

    def test_tools_only(self):
        result = build_usage_parts(5, 0, 0)
        assert result == ["5 tools"]

    def test_tokens_only(self):
        result = build_usage_parts(0, 12000, 3500)
        assert result == ["↑12,000 ↓3,500 tokens"]

    def test_tools_and_tokens(self):
        result = build_usage_parts(5, 12000, 3500)
        assert result == ["5 tools", "↑12,000 ↓3,500 tokens"]

    def test_single_tool(self):
        result = build_usage_parts(1, 100, 50)
        assert result[0] == "1 tools"

    def test_large_numbers_comma_formatted(self):
        result = build_usage_parts(0, 1000000, 500000)
        assert result == ["↑1,000,000 ↓500,000 tokens"]

    def test_completion_tokens_included_even_when_zero(self):
        """When prompt_tokens > 0, completion_tokens=0 still shows."""
        result = build_usage_parts(0, 100, 0)
        assert result == ["↑100 ↓0 tokens"]


# ── _usage_suffix ─────────────────────────────────────────


class TestUsageSuffix:
    def test_empty(self):
        r = NodeResult(node_id="a", task="", output="", exit_code=0, duration=0)
        assert _usage_suffix(r) == ""

    def test_tools_only(self):
        r = NodeResult(
            node_id="a", task="", output="", exit_code=0, duration=0,
            tool_calls=3,
        )
        assert _usage_suffix(r) == " (3 tools)"

    def test_tokens_only(self):
        r = NodeResult(
            node_id="a", task="", output="", exit_code=0, duration=0,
            prompt_tokens=12000, completion_tokens=3500,
        )
        assert _usage_suffix(r) == " (↑12,000 ↓3,500 tokens)"

    def test_tools_and_tokens(self):
        r = NodeResult(
            node_id="a", task="", output="", exit_code=0, duration=0,
            tool_calls=5, prompt_tokens=12000, completion_tokens=3500,
        )
        assert _usage_suffix(r) == " (5 tools, ↑12,000 ↓3,500 tokens)"


# ── summarize / detailed_summarize ────────────────────────


class TestSummarize:
    def test_includes_usage_suffix(self):
        r = NodeResult(
            node_id="n1", task="Do thing", output="ok",
            exit_code=0, duration=1.5,
            tool_calls=3, prompt_tokens=1000, completion_tokens=500,
        )
        result = summarize("test", [r])
        assert "3 tools" in result
        assert "↑1,000" in result

    def test_no_usage_when_zero(self):
        r = NodeResult(
            node_id="n1", task="Do thing", output="ok",
            exit_code=0, duration=1.5,
        )
        result = summarize("test", [r])
        assert "()" not in result

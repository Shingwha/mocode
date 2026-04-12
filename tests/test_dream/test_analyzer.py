"""Tests for DreamAnalyzer"""

import json

import pytest

from mocode.core.dream.analyzer import DreamAnalyzer, EditDirective


class TestParseDirectives:
    """Test parsing of LLM output into EditDirective list."""

    def test_parse_valid_json(self):
        content = json.dumps([
            {
                "target": "SOUL.md",
                "action": "add",
                "content": "New guideline",
                "reasoning": "Observed pattern in conversations",
            },
            {
                "target": "USER.md",
                "action": "remove",
                "content": "Old info",
                "reasoning": "Outdated",
            },
        ])

        result = DreamAnalyzer._parse_directives(content)
        assert len(result) == 2
        assert result[0].target == "SOUL.md"
        assert result[0].action == "add"
        assert result[1].target == "USER.md"
        assert result[1].action == "remove"

    def test_parse_empty_array(self):
        result = DreamAnalyzer._parse_directives("[]")
        assert result == []

    def test_parse_with_code_fence(self):
        content = "```json\n[{\"target\": \"SOUL.md\", \"action\": \"add\", \"content\": \"test\", \"reasoning\": \"test\"}]\n```"
        result = DreamAnalyzer._parse_directives(content)
        assert len(result) == 1

    def test_parse_invalid_json(self):
        result = DreamAnalyzer._parse_directives("not json at all")
        assert result == []

    def test_parse_non_array_json(self):
        result = DreamAnalyzer._parse_directives('{"target": "SOUL.md"}')
        assert result == []

    def test_parse_filters_invalid_targets(self):
        content = json.dumps([
            {"target": "INVALID.md", "action": "add", "content": "test", "reasoning": "test"},
            {"target": "SOUL.md", "action": "add", "content": "valid", "reasoning": "valid"},
        ])
        result = DreamAnalyzer._parse_directives(content)
        assert len(result) == 1
        assert result[0].target == "SOUL.md"

    def test_parse_filters_invalid_actions(self):
        content = json.dumps([
            {"target": "SOUL.md", "action": "update", "content": "test", "reasoning": "test"},
        ])
        result = DreamAnalyzer._parse_directives(content)
        assert result == []

    def test_parse_filters_empty_content(self):
        content = json.dumps([
            {"target": "SOUL.md", "action": "add", "content": "", "reasoning": "test"},
        ])
        result = DreamAnalyzer._parse_directives(content)
        assert result == []

    def test_parse_handles_non_dict_items(self):
        content = json.dumps(["not a dict", 42, None])
        result = DreamAnalyzer._parse_directives(content)
        assert result == []


@pytest.fixture
def mock_provider():
    """Create a mock provider for analyzer tests."""
    from unittest.mock import AsyncMock

    provider = AsyncMock()

    async def mock_call(*args, **kwargs):
        # Create a mock response with EditDirective JSON
        class MockMessage:
            content = json.dumps([
                {
                    "target": "MEMORY.md",
                    "action": "add",
                    "content": "- User prefers TypeScript over JavaScript",
                    "reasoning": "Observed in multiple conversations",
                }
            ])
            tool_calls = None

        class MockChoice:
            message = MockMessage()

        class MockResponse:
            choices = [MockChoice()]

        return MockResponse()

    provider.call = mock_call
    return provider


@pytest.mark.asyncio
async def test_analyze_returns_directives(mock_provider):
    analyzer = DreamAnalyzer(mock_provider)
    directives = await analyzer.analyze(
        summaries=["Summary of conversation"],
        soul="Soul content",
        user="User content",
        memory="Memory content",
    )
    assert len(directives) == 1
    assert directives[0].target == "MEMORY.md"


@pytest.mark.asyncio
async def test_analyze_handles_provider_error():
    from unittest.mock import AsyncMock

    provider = AsyncMock()
    provider.call = AsyncMock(side_effect=Exception("API error"))

    analyzer = DreamAnalyzer(provider)
    directives = await analyzer.analyze(
        summaries=["Summary"],
        soul="Soul",
        user="User",
        memory="Memory",
    )
    assert directives == []

"""Tests for DreamAgent — v0.2 (Response DTO + ToolRegistry instance)"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from mocode.dream.agent import DreamAgent, DreamAgentResult
from mocode.provider import Response, ToolCall
from mocode.tool import Tool, ToolRegistry


def _make_tool_call(name: str, arguments: dict, call_id: str = "tc_1"):
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


@pytest.fixture
def dream_registry():
    """Registry with read, edit, append tools for dream."""
    registry = ToolRegistry()
    registry.register(Tool("read", "Read", {"path": "string"}, lambda a: "file content"))
    registry.register(Tool("edit", "Edit", {"path": "string", "old": "string", "new": "string"}, lambda a: "ok"))
    registry.register(Tool("append", "Append", {"path": "string", "content": "string"}, lambda a: "ok"))
    return registry


@pytest.mark.asyncio
async def test_run_no_edits_needed(dream_registry):
    provider = AsyncMock()
    response = Response(content="No changes needed.")
    provider.call = AsyncMock(return_value=response)

    agent = DreamAgent(provider, dream_registry, max_tool_calls=10)
    result = await agent.run(
        summaries=["Summary of conversation"],
        soul="Soul content",
        user="User content",
        memory="Memory content",
    )

    assert result.tool_calls_made == 0
    assert result.edits_made == 0
    assert result.had_error is False


@pytest.mark.asyncio
async def test_run_single_edit(dream_registry):
    provider = AsyncMock()

    read_tc = _make_tool_call("read", {"path": "MEMORY.md"}, "tc_1")
    response1 = Response(tool_calls=[read_tc])

    edit_tc = _make_tool_call("edit", {"path": "MEMORY.md", "old": "# Memory", "new": "# Memory\n- New fact"}, "tc_2")
    response2 = Response(tool_calls=[edit_tc])

    response3 = Response(content="Done editing.")

    provider.call = AsyncMock(side_effect=[response1, response2, response3])

    agent = DreamAgent(provider, dream_registry, max_tool_calls=10)
    result = await agent.run(
        summaries=["User prefers dark mode"],
        soul="Soul",
        user="User",
        memory="Memory content",
    )

    assert result.tool_calls_made == 2
    assert result.edits_made == 1
    assert result.had_error is False


@pytest.mark.asyncio
async def test_run_max_tool_calls_limit(dream_registry):
    provider = AsyncMock()

    tc = _make_tool_call("read", {"path": "MEMORY.md"}, "tc_loop")
    looping_response = Response(tool_calls=[tc])

    provider.call = AsyncMock(return_value=looping_response)

    agent = DreamAgent(provider, dream_registry, max_tool_calls=3)
    result = await agent.run(
        summaries=["Summary"],
        soul="Soul",
        user="User",
        memory="Memory",
    )

    assert result.tool_calls_made == 3


@pytest.mark.asyncio
async def test_run_provider_error(dream_registry):
    provider = AsyncMock()
    provider.call = AsyncMock(side_effect=Exception("API error"))

    agent = DreamAgent(provider, dream_registry, max_tool_calls=10)
    result = await agent.run(
        summaries=["Summary"],
        soul="Soul",
        user="User",
        memory="Memory",
    )

    assert result.had_error is True
    assert result.tool_calls_made == 0


@pytest.mark.asyncio
async def test_run_resolves_relative_paths():
    """Verify relative memory file paths are resolved to MEMORY_DIR."""
    provider = AsyncMock()

    registry = ToolRegistry()
    registry.register(Tool("read", "Read", {"path": "string"}, lambda a: "file content"))
    registry.register(Tool("edit", "Edit", {"path": "string", "old": "string", "new": "string"}, lambda a: "ok"))
    registry.register(Tool("append", "Append", {"path": "string", "content": "string"}, lambda a: "ok"))

    read_tc = _make_tool_call("read", {"path": "SOUL.md"}, "tc_1")
    response1 = Response(tool_calls=[read_tc])
    response2 = Response(content="Done.")

    provider.call = AsyncMock(side_effect=[response1, response2])

    agent = DreamAgent(provider, registry, max_tool_calls=10)

    # Track what path was actually used
    actual_paths = []
    original_run = registry.run
    def tracking_run(name, args):
        actual_paths.append(args.get("path", ""))
        return original_run(name, args)
    registry.run = tracking_run

    await agent.run(
        summaries=["Summary"],
        soul="Soul",
        user="User",
        memory="Memory",
    )

    assert len(actual_paths) == 1
    assert "memory" in actual_paths[0].replace("\\", "/").lower()

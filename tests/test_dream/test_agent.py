"""Tests for DreamAgent"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.core.dream.agent import DreamAgent, DreamAgentResult


def _make_mock_response(content=None, tool_calls=None):
    """Create a mock LLM response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop" if not tool_calls else "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(name: str, arguments: dict, call_id: str = "tc_1"):
    """Create a mock tool call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


@pytest.mark.asyncio
async def test_run_no_edits_needed():
    """Agent analyzes and decides no edits needed (no tool calls)."""
    provider = AsyncMock()
    response = _make_mock_response(content="No changes needed.")
    provider.call = AsyncMock(return_value=response)

    agent = DreamAgent(provider, max_tool_calls=10)
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
async def test_run_single_edit():
    """Agent reads a file then edits it."""
    provider = AsyncMock()

    read_tc = _make_tool_call("read", {"path": "MEMORY.md"}, "tc_1")
    response1 = _make_mock_response(tool_calls=[read_tc])

    edit_tc = _make_tool_call("edit", {
        "path": "MEMORY.md",
        "old_string": "# Long-term Memory",
        "new_string": "# Long-term Memory\n- New fact",
    }, "tc_2")
    response2 = _make_mock_response(tool_calls=[edit_tc])

    response3 = _make_mock_response(content="Done editing.")

    provider.call = AsyncMock(side_effect=[response1, response2, response3])

    agent = DreamAgent(provider, max_tool_calls=10)

    with patch("mocode.core.dream.agent.ToolRegistry.run") as mock_run:
        mock_run.side_effect = [
            "1 | # Long-term Memory\n2 | Old content",
            "ok",
        ]

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
async def test_run_max_tool_calls_limit():
    """Agent stops at max_tool_calls even if LLM keeps calling tools."""
    provider = AsyncMock()

    tc = _make_tool_call("read", {"path": "MEMORY.md"}, "tc_loop")
    looping_response = _make_mock_response(tool_calls=[tc])

    provider.call = AsyncMock(return_value=looping_response)

    agent = DreamAgent(provider, max_tool_calls=3)

    with patch("mocode.core.dream.agent.ToolRegistry.run", return_value="file content"):
        result = await agent.run(
            summaries=["Summary"],
            soul="Soul",
            user="User",
            memory="Memory",
        )

    assert result.tool_calls_made == 3


@pytest.mark.asyncio
async def test_run_provider_error():
    """Agent handles provider error gracefully."""
    provider = AsyncMock()
    provider.call = AsyncMock(side_effect=Exception("API error"))

    agent = DreamAgent(provider, max_tool_calls=10)
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
    """Verify that relative memory file paths are resolved to MEMORY_DIR."""
    provider = AsyncMock()

    read_tc = _make_tool_call("read", {"path": "SOUL.md"}, "tc_1")
    response1 = _make_mock_response(tool_calls=[read_tc])
    response2 = _make_mock_response(content="Done.")

    provider.call = AsyncMock(side_effect=[response1, response2])

    agent = DreamAgent(provider, max_tool_calls=10)

    with patch("mocode.core.dream.agent.ToolRegistry.run") as mock_run:
        mock_run.return_value = "file content"
        await agent.run(
            summaries=["Summary"],
            soul="Soul",
            user="User",
            memory="Memory",
        )

        call_args = mock_run.call_args
        path_arg = call_args[0][1]["path"]
        assert "memory" in path_arg.replace("\\", "/").lower()


@pytest.mark.asyncio
async def test_run_multiple_edits():
    """Agent makes multiple read+edit cycles."""
    provider = AsyncMock()

    read_tc1 = _make_tool_call("read", {"path": "USER.md"}, "tc_1")
    response1 = _make_mock_response(tool_calls=[read_tc1])

    edit_tc1 = _make_tool_call("edit", {
        "path": "USER.md",
        "old_string": "# User",
        "new_string": "# User\n- Prefers dark mode",
    }, "tc_2")
    response2 = _make_mock_response(tool_calls=[edit_tc1])

    read_tc2 = _make_tool_call("read", {"path": "MEMORY.md"}, "tc_3")
    response3 = _make_mock_response(tool_calls=[read_tc2])

    edit_tc2 = _make_tool_call("edit", {
        "path": "MEMORY.md",
        "old_string": "# Memory",
        "new_string": "# Memory\n- Important fact",
    }, "tc_4")
    response4 = _make_mock_response(tool_calls=[edit_tc2])

    response5 = _make_mock_response(content="All done.")

    provider.call = AsyncMock(
        side_effect=[response1, response2, response3, response4, response5]
    )

    agent = DreamAgent(provider, max_tool_calls=10)

    with patch("mocode.core.dream.agent.ToolRegistry.run") as mock_run:
        mock_run.side_effect = [
            "1 | # User",
            "ok",
            "1 | # Memory",
            "ok",
        ]

        result = await agent.run(
            summaries=["User prefers dark mode", "Important fact noted"],
            soul="Soul",
            user="User",
            memory="Memory",
        )

    assert result.tool_calls_made == 4
    assert result.edits_made == 2
    assert result.had_error is False


@pytest.mark.asyncio
async def test_run_handles_bad_tool_args():
    """Agent handles malformed tool call arguments."""
    provider = AsyncMock()

    bad_tc = MagicMock()
    bad_tc.id = "tc_bad"
    bad_tc.function.name = "read"
    bad_tc.function.arguments = "not json"
    response1 = _make_mock_response(tool_calls=[bad_tc])
    response2 = _make_mock_response(content="Done.")

    provider.call = AsyncMock(side_effect=[response1, response2])

    agent = DreamAgent(provider, max_tool_calls=10)

    with patch("mocode.core.dream.agent.ToolRegistry.run", return_value="file content"):
        result = await agent.run(
            summaries=["Summary"],
            soul="Soul",
            user="User",
            memory="Memory",
        )

    assert result.tool_calls_made == 1
    assert result.had_error is False

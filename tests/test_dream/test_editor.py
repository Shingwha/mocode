"""Tests for DreamEditor"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.core.dream.analyzer import EditDirective
from mocode.core.dream.editor import DreamEditor


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
async def test_edit_no_directives():
    provider = AsyncMock()
    editor = DreamEditor(provider, max_tool_calls=10)
    result = await editor.edit([])
    assert result == 0
    provider.call.assert_not_called()


@pytest.mark.asyncio
async def test_edit_single_add_directive():
    provider = AsyncMock()
    # First call: LLM reads file, Second call: LLM edits, Third call: LLM done
    read_tc = _make_tool_call("read", {"path": "MEMORY.md"}, "tc_1")
    response1 = _make_mock_response(tool_calls=[read_tc])

    edit_tc = _make_tool_call("edit", {
        "path": "MEMORY.md",
        "old": "# Long-term Memory",
        "new": "# Long-term Memory\n- New fact",
    }, "tc_2")
    response2 = _make_mock_response(tool_calls=[edit_tc])

    response3 = _make_mock_response(content="Done editing.")

    provider.call = AsyncMock(side_effect=[response1, response2, response3])

    editor = DreamEditor(provider, max_tool_calls=10)

    # Patch ToolRegistry.run to return fake results
    with patch("mocode.core.dream.editor.ToolRegistry.run") as mock_run:
        mock_run.side_effect = [
            "1 | # Long-term Memory\n2 | Old content",  # read result
            "ok",  # edit result
        ]

        directives = [
            EditDirective(
                target="MEMORY.md",
                action="add",
                content="- New fact",
                reasoning="Test reasoning",
            )
        ]
        result = await editor.edit(directives)
        assert result == 2  # read + edit


@pytest.mark.asyncio
async def test_edit_max_tool_calls_limit():
    provider = AsyncMock()

    # LLM keeps making tool calls
    tc = _make_tool_call("read", {"path": "MEMORY.md"}, "tc_loop")
    looping_response = _make_mock_response(tool_calls=[tc])
    final_response = _make_mock_response(content="Done.")

    provider.call = AsyncMock(return_value=looping_response)

    editor = DreamEditor(provider, max_tool_calls=3)

    with patch("mocode.core.dream.editor.ToolRegistry.run", return_value="file content"):
        result = await editor.edit([
            EditDirective("MEMORY.md", "add", "content", "reason"),
        ])
        # Should stop at max_tool_calls
        assert result == 3


@pytest.mark.asyncio
async def test_edit_provider_error():
    provider = AsyncMock()
    provider.call = AsyncMock(side_effect=Exception("API error"))

    editor = DreamEditor(provider, max_tool_calls=10)
    result = await editor.edit([
        EditDirective("MEMORY.md", "add", "content", "reason"),
    ])
    assert result == 0


@pytest.mark.asyncio
async def test_edit_resolves_relative_paths():
    """Verify that relative memory file paths are resolved to MEMORY_DIR."""
    provider = AsyncMock()

    read_tc = _make_tool_call("read", {"path": "SOUL.md"}, "tc_1")
    response1 = _make_mock_response(tool_calls=[read_tc])
    response2 = _make_mock_response(content="Done.")

    provider.call = AsyncMock(side_effect=[response1, response2])

    editor = DreamEditor(provider, max_tool_calls=10)

    with patch("mocode.core.dream.editor.ToolRegistry.run") as mock_run:
        mock_run.return_value = "file content"
        await editor.edit([
            EditDirective("SOUL.md", "add", "content", "reason"),
        ])
        # Check that path was resolved
        call_args = mock_run.call_args
        path_arg = call_args[0][1]["path"]
        assert "memory" in path_arg.replace("\\", "/").lower()

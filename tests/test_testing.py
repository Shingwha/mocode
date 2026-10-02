"""mocode.testing — the public doubles and helpers plugin tests are written with.

The doubles themselves are exercised by every other test module (they are the
"model" those tests talk to); what is asserted here is the reading side —
``collect`` / ``terminal`` / ``events_of_type`` — and the script constructors
``say`` / ``call_tool`` that plugin authors write their scenarios with.
"""

from __future__ import annotations

import pytest

from mocode.core.events import RunFinished, TextDelta, ToolCallFinished
from mocode.core.tool import ToolRegistry
from mocode.testing import (
    ARG_FRAGMENT,
    MockProvider,
    call_tool,
    collect,
    events_of_type,
    response_to_chunks,
    say,
    terminal,
)

from .conftest import echo_tool, make_agent


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(echo_tool())
    return registry


class TestScriptConstructors:
    def test_say_is_a_plain_text_response(self):
        response = say("hello")
        assert response.content == "hello"
        assert not response.tool_calls

    def test_call_tool_serialises_dict_args(self):
        response = call_tool("read", {"path": "a.txt"})
        [call] = response.tool_calls
        assert call.name == "read"
        assert call.arguments == '{"path": "a.txt"}'

    def test_call_tool_arguments_arrive_in_fragments(self):
        arguments = '{"x": "0123456789"}'
        chunks = list(response_to_chunks(call_tool("t", {"x": "0123456789"})))
        fragments = [
            c.tool_calls[0].arguments for c in chunks if c.tool_calls and c.tool_calls[0].arguments
        ]
        assert "".join(fragments) == arguments
        assert all(len(f) <= ARG_FRAGMENT for f in fragments)


class TestReadingATurn:
    async def test_collect_and_terminal_read_a_whole_turn(self):
        agent = make_agent(provider=MockProvider([say("done")]), system_prompt="t")
        events = await collect(agent.start("hi").subscribe())
        assert isinstance(terminal(events), RunFinished)
        assert terminal(events).content == "done"
        assert [e.text for e in events_of_type(events, TextDelta)] == ["done"]

    async def test_a_script_drives_a_tool_call_then_an_answer(self):
        agent = make_agent(
            provider=MockProvider([call_tool("echo", {"value": "1"}), say("after")]),
            tools=_echo_registry(),
            system_prompt="t",
        )
        events = await collect(agent.start("hi").subscribe())
        [finished] = events_of_type(events, ToolCallFinished)
        assert finished.name == "echo"
        assert finished.status == "ok"
        assert terminal(events).content == "after"

    def test_terminal_refuses_a_stream_without_an_end(self):
        with pytest.raises(AssertionError):
            terminal([TextDelta("streaming...")])

    def test_terminal_refuses_more_than_one_end(self):
        with pytest.raises(AssertionError):
            terminal([RunFinished(content="a"), RunFinished(content="b")])

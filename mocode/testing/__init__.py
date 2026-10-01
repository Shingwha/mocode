"""Public test doubles and assertion helpers — test a plugin against a script.

Everything a plugin author needs to drive a conversation without a real model
lives here: :class:`MockProvider` replays canned responses as chunk streams,
:say: / :call_tool: build the responses a script is made of, and
:collect: / :terminal: / :events_of_type: read back what the run emitted.

A test script states what the "model" does, the way ``tests/test_plugins.py``
in the repository does it::

    from mocode.testing import MockProvider, call_tool, say, terminal

    provider = MockProvider([call_tool("my_tool", {"path": "x"}), say("done")])
    conversation.agent.provider = provider
    events = await collect(conversation.stream("go"))
    assert terminal(events).content == "done"

Importing this package pulls in ``mocode.core`` — it is a testing dependency,
never a runtime one, so it is deliberately not reachable from ``import mocode``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Iterable

from mocode.core.events import RunFailed, RunFinished

from .providers import (
    ARG_FRAGMENT,
    MockProvider,
    SlowProvider,
    call_tool,
    response_to_chunks,
    say,
    tool_call_response,
)

if TYPE_CHECKING:
    from mocode.core.events import Event

#: The event classes that end a turn — exactly one per run, by contract.
_TERMINAL = (RunFinished, RunFailed)

__all__ = [
    "ARG_FRAGMENT",
    "MockProvider",
    "SlowProvider",
    "call_tool",
    "collect",
    "events_of_type",
    "response_to_chunks",
    "say",
    "terminal",
    "tool_call_response",
]


async def collect(agen: AsyncIterator["Event"]) -> list["Event"]:
    """Drain an async iterator into a list — the usual way to read a turn."""
    return [event async for event in agen]


def events_of_type(events: Iterable["Event"], cls: type) -> list:
    """The events that are instances of *cls*, in stream order."""
    return [event for event in events if isinstance(event, cls)]


def terminal(events: Iterable["Event"]) -> "Event":
    """The one event that ended the turn — ``RunFinished`` or ``RunFailed``.

    Every turn emits exactly one terminating event, so anything else is a bug
    in the test: zero means the stream was drained before the turn ended (or
    through a filter that skipped it), more than one means the events span
    several turns.
    """
    found = [event for event in events if isinstance(event, _TERMINAL)]
    seen = ", ".join(sorted({type(event).__name__ for event in events})) or "empty"
    assert found, f"no terminal event among: {seen}"
    assert len(found) == 1, f"expected one terminal event, got {len(found)}"
    return found[0]

"""Two agents, one timeline — a sub-agent built with ``AgentLoop.derive()``.

The child keeps the parent's capability set but nothing of its mutable
state: its own tool registry, its own policy, a cheaper provider, a fresh
history. It borrows the parent's channel, so both runs land on one ordered
timeline — while each agent's own ``Turn`` and ``state`` stay scoped to its
run. Run it::

    uv run python examples/core/nested.py
"""

from __future__ import annotations

import asyncio

from mocode.core import AgentConfig, AgentLoop, HookRunner, Tool, ToolRegistry
from mocode.core.events import RunStarted
from mocode.core.provider import Chunk, ModelSpec, Usage


class ScriptedProvider:
    """A provider that answers with one fixed reply — name and price differ."""

    def __init__(self, model: str, reply: str):
        self._model = model
        self._reply = reply

    @property
    def model(self) -> str:
        return self._model

    def is_retriable(self, exc: Exception) -> bool:
        return False

    async def stream(self, messages, system, tools, max_tokens):
        yield Chunk(text=self._reply, usage=Usage(3, 5), finish_reason="stop")


def lookup(args: dict) -> str:
    return "3 events, 2 files, 1 answer"


async def main() -> None:
    tools = ToolRegistry().register(
        Tool(
            "lookup",
            "Look a fact up.",
            {"query": {"type": "string", "description": "what to look up"}},
            lookup,
            tags=frozenset({"research"}),
        )
    )
    parent = AgentLoop(
        provider=ScriptedProvider("big-1", "Delegating to the cheap model."),
        system_prompt="You orchestrate; you do not do the reading yourself.",
        tools=tools,
        hooks=HookRunner(),
        config=AgentConfig(),
    )

    # The child: same capability set (copied, not shared — disabling a tool
    # here reaches nothing of the parent's), a cheaper model, the parent's
    # channel so both runs share one timeline.
    child = parent.derive(
        provider=ScriptedProvider("cheap-1", "Read everything; report back."),
        model=ModelSpec(name="cheap-1"),
        channel=parent.channel,
    )

    reader = parent.channel.subscribe()  # one reader for both runs

    result = await child.run_with_messages([{"role": "user", "content": "summarize"}])
    if result.had_error:  # run_with_messages never raises; the flag is the answer
        print(f"child failed: {result.content}")

    # The run is over, so everything is already queued: drain without blocking.
    seen = []
    while True:
        event = reader.take()
        if event is None:
            break
        seen.append(event)
    reader.close()
    for event in seen:
        print(f"  {event.seq:>3}  {event.run_id or '-'}  {event.summary()}")

    child_runs = [e for e in seen if isinstance(e, RunStarted) and e.model == "cheap-1"]
    print(f"\nchild answer: {result.content!r}")
    print(f"child events on the parent's timeline: {len(child_runs)} run, {len(seen)} total")
    print(f"parent state still idle: {parent.state.status}")  # run-scoped, not channel-scoped

    child.close()
    parent.close()


if __name__ == "__main__":
    asyncio.run(main())

"""A complete agent built from ``mocode.core`` alone.

No host, no plugins, no config file — the kernel is the whole engine. A
provider, a tool, a hook, five constructor arguments, and the run is
observable as events. Run it::

    uv run python examples/core/minimal.py
"""

from __future__ import annotations

import asyncio

from mocode.core import (
    AgentConfig,
    AgentLoop,
    AgentHook,
    HookRunner,
    Tool,
    ToolRegistry,
)
from mocode.core.hook import ToolCallContext
from mocode.core.provider import Chunk, Usage


class EchoProvider:
    """The whole Provider protocol: three members, no SDK.

    Streams one canned reply in two chunks, the way a real backend would.
    Swap this class for an HTTP client and nothing else in the file changes —
    the protocol is structural, not inherited.
    """

    def __init__(self, reply: str):
        self._reply = reply

    @property
    def model(self) -> str:
        return "echo-1"

    def is_retriable(self, exc: Exception) -> bool:
        return False  # nothing here fails transiently

    async def stream(self, messages, system, tools, max_tokens):
        half = len(self._reply) // 2
        yield Chunk(text=self._reply[:half])
        yield Chunk(text=self._reply[half:], usage=Usage(7, 11), finish_reason="stop")


def word_count(args: dict) -> str:
    return str(len(args["text"].split()))


class Auditor(AgentHook):
    """A hook is the interception channel; this one just observes."""

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        print(f"      hook: {ctx.tool_name} <- {ctx.tool_args}")


async def main() -> None:
    tools = ToolRegistry().register(
        Tool(
            "word_count",
            "Count the words in a text.",
            {"text": {"type": "string", "description": "the text to count"}},
            word_count,
            tags=frozenset({"text"}),
        )
    )
    agent = AgentLoop(
        provider=EchoProvider("Count this sentence for me, kernel."),
        system_prompt="You are a minimal example agent.",
        tools=tools,
        hooks=HookRunner([Auditor()]),
        config=AgentConfig(tool_timeout=30, max_iterations=5),
    )

    turn = agent.start("hello")
    async for event in turn.subscribe():  # this turn's events, replay included
        print(f"  {event.seq:>3}  {event.summary()}")

    terminal = await turn.wait()
    print(f"\nanswer: {terminal.content!r}")
    print(f"usage:  {agent.state.usage.to_dict()}")
    agent.close()


if __name__ == "__main__":
    asyncio.run(main())

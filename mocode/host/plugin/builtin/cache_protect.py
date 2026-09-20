"""cache-protect plugin — a conversation's request prefix is pinned; what
changes is announced, not applied.

Two halves of every request are frozen for a session's lifetime: the system
prompt (the host materializes it before the first request and reinstates a
stored one on resume) and the tool interface (``ToolRegistry.freeze``).
Holding both still is what makes a provider's prefix cache survive turn after
turn — and it leaves the world free to move: an edited AGENTS.md, a tool
switched off, a tool's description rewritten, a tool registered mid-session.
This plugin is how the model hears about any of it.

Two triggers, one notice:

  - **A resume** (``ConversationChanged``): the host has just reinstated the
    session's frozen prompt and interface, so both are diffed against the
    world as it stands and the notice lands at the tail of the history —
    before the next user message, with the cached prefix above it untouched.
  - **The start of every turn** (the first iteration's ``before_iteration``):
    the same diff, inserted just before the message that opens the turn.

The prompt half can only have moved at a resume or a rebuild (a rebuild
clears the baselines — the model was just re-told everything), and the tool
half can move any time, so the turn-start trigger checks both and costs one
render. A change reverted before the turn that would announce it is never
announced; the baseline is what the model was last told, and it lives in
``ctx.plugin_state`` so it survives save and resume.

Three shapes for a tool change:

  - switched off / on — a state line. The schema never moved, so there is
    nothing to diff; and a call to a switched-off tool is refused by the
    registry's own projection, which is the correction if the model tries.
  - schema rewritten — a diff of the two schemas, built fresh from the Tool
    object (never the cached projection, which an in-place edit would leave
    stale).
  - registered after the freeze — announced with its schema: callable through
    the registry even though the pinned payload does not offer it, and it
    enters the payload at the next rebuild or session.

With the interface unpinned (an embedder that opted out) the tool half is a
silent no-op; the prompt half still runs, because the frozen prompt is part
of the session model either way.
"""

from __future__ import annotations

import difflib
import json
from typing import TYPE_CHECKING, Any

from ....core.hook import AgentHook
from ....core.tool import ToolRegistry
from ...events import ConversationChanged
from ...prompt import build_system_prompt
from ..base import Plugin
from ..context import HostContext

if TYPE_CHECKING:
    from ....core.events import Event
    from ....core.hook import IterationContext

NAME = "cache-protect"

_HEADER = "[context update — the environment changed since the system prompt was written]"


def unified(label: str, before: str, after: str) -> str:
    """One git-diff-style block: what *label* read like, what it reads now.

    The notice's only format — the prompt and a tool schema read the same
    way, and both measure the change rather than the size of the thing that
    changed.
    """
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"{label} as last told",
        tofile=f"{label} as it stands now",
        lineterm="",
    )
    return "\n".join(diff)


def _pretty(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False)


def _fresh_schema(tools: ToolRegistry, name: str) -> dict[str, Any] | None:
    """A tool's schema, built from the object — never the cached projection,
    which a plugin mutating its tool in place would leave stale."""
    tool = tools.get(name)
    return tool.to_schema() if tool is not None else None


class _Watcher(AgentHook):
    """One conversation's watcher — its baseline lives in the conversation's
    plugin state, so it survives a save and a resume."""

    def __init__(self, ctx: HostContext):
        self._ctx = ctx

    # ── the two triggers ────────────────────────────────────

    async def on_event(self, event: "Event") -> None:
        if isinstance(event, ConversationChanged):
            # A resume (or /clear): the frozen artifacts were just put back.
            self._announce()

    async def before_iteration(self, ctx: "IterationContext") -> None:
        if ctx.iteration == 1:
            self._announce(messages=ctx.messages)

    # ── the diff ────────────────────────────────────────────

    def _announce(self, messages: list[dict] | None = None) -> None:
        host = self._ctx
        agent = host.agent
        if agent is None:
            return
        state = host.plugin_state(NAME)
        tools = host.tools

        # The prompt: what the model was last told (the frozen string, until
        # the first announcement) against the world as it renders now.
        told_prompt = state.get("prompt")
        if told_prompt is None:
            told_prompt = agent.system_prompt
        fresh_prompt = build_system_prompt(host)

        # The tools: what the interface still offers against what the live
        # registry now says. Unpinned, both are the same live read.
        pinned = {s["function"]["name"]: s for s in tools.all_schemas()}
        told_tools: dict[str, dict] = state.get("tools")
        if told_tools is None:
            told_tools = dict(pinned)

        entries: list[str] = []
        if told_prompt != fresh_prompt:
            entries.append(unified("the system prompt", told_prompt, fresh_prompt))
        entries += self._tool_entries(tools, pinned, told_tools)
        if not entries:
            return

        notice = _HEADER + "\n" + "\n\n".join(entries)
        if messages is None:
            agent.messages.append({"role": "user", "content": notice})
        else:
            self._insert_before_user(messages, notice)

        state["prompt"] = fresh_prompt
        state["tools"] = {
            name: schema
            for name in tools.names()
            if (schema := _fresh_schema(tools, name)) is not None
        }

    def _tool_entries(
        self,
        tools: ToolRegistry,
        pinned: dict[str, dict],
        told: dict[str, dict],
    ) -> list[str]:
        current = set(tools.names())
        entries: list[str] = []

        for name in sorted(current - set(told)):
            schema = _fresh_schema(tools, name)
            if schema is not None and name not in pinned:
                # Announced with its schema: usable through the registry even
                # though the pinned payload does not offer it.
                entries.append(
                    f"tool '{name}' is now available:\n"
                    + unified(f"tool '{name}'", "", _pretty(schema))
                )
            else:
                entries.append(f"tool '{name}' is now available")

        for name in sorted(set(told) - current):
            if tools.get(name) is None:
                entries.append(f"tool '{name}' was removed")
            else:
                entries.append(f"tool '{name}' is now disabled")

        for name in sorted(set(told) & current):
            schema = _fresh_schema(tools, name)
            if schema is not None and schema != told[name]:
                entries.append(
                    f"tool '{name}' changed:\n"
                    + unified(f"tool '{name}'", _pretty(told[name]), _pretty(schema))
                )
        return entries

    @staticmethod
    def _insert_before_user(messages: list[dict], notice: str) -> None:
        """The notice lands just before the message that opens this turn, so
        everything above it — the cached prefix — stays byte-identical."""
        entry = {"role": "user", "content": notice}
        if messages and messages[-1].get("role") == "user":
            messages.insert(len(messages) - 1, entry)
        else:
            messages.append(entry)


class CacheProtectPlugin(Plugin):
    name = NAME
    description = (
        "Keeps a session's request prefix pinned; changes arrive as diff notices"
    )

    def build(self, ctx: HostContext) -> None:
        ctx.hooks.append(_Watcher(ctx))


PLUGIN = CacheProtectPlugin()

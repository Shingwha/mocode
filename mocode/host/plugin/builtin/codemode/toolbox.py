"""The script's ``tools`` facade — resolution, calls and the catalogue helpers.

:class:`ToolBox` is the ``tools`` name a script runs with: attribute and
subscript access bind one tool call through the exact registered name —
the single spelling of a tool — the facade forgives the built-in names
(``tools.describe_tool`` and friends return the built-in itself), and
``codemode`` itself is never callable. The catalogue helpers — :func:`tool_entries` and
:func:`describe_tool_entry` — read the same program-audience projection
the box is built from, so what the catalogue lists and what a script may
call cannot drift apart.

Every call a script makes goes through the dispatcher with
``origin="program"`` — that is the program-origin contract: the calls are
observable on the event stream, but they never enter ``messages`` and
never fold into the turn's ``tool_calls_made`` count.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .....core.tool import ToolRegistry
from .result import Result, ToolCallError, _PARALLEL_LIMIT
from .runtime import CodemodeError
from .search import normalize

if TYPE_CHECKING:
    from .....core.dispatch import ToolDispatcher

__all__ = ["ToolBox", "describe_tool_entry", "tool_entries"]

#: Sentinel :meth:`ToolBox._resolve` returns when a name is not a tool but
#: a built-in the facade forgives — :meth:`ToolBox._bind` then hands the
#: built-in itself back instead of a call.
_FACADE = object()


class ToolBox:
    """The script's ``tools`` — attribute/subscript access binds a tool call.

    Both entry points run the same resolution: the exact registered name,
    the single spelling of a tool. An MCP tool answers only to its full
    ``mcp__<server>__<tool>`` name — never to a bare short name — so two
    servers with same-named tools cannot collide, and the bare namespace
    belongs to the built-ins (``store``, ``describe_tool``, …) even when a
    registered tool ends in it. A name no tool and no built-in claims is
    unknown, and the error reports the candidate full names. ``codemode``
    itself is never callable from a script. ``dir(tools)`` and the
    catalogue list the tools registered when the box was created;
    resolution itself reads the live registry.

    An optional ``semaphore`` caps how many of this box's calls run at
    once: every call acquires it around the dispatcher, so a fan-out
    queues instead of running all at once. ``None`` (the default) leaves
    calls unlimited.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: "ToolDispatcher",
        parent_call_id: str,
        semaphore: asyncio.Semaphore | None = None,
        facade: dict | None = None,
    ):
        self._registry = registry
        self._dispatcher = dispatcher
        self._parent_call_id = parent_call_id
        self._semaphore = semaphore
        #: Counts every ``dispatcher.run`` the box makes — reported as the
        #: result's ``tool_calls``.
        self.calls = 0
        self._facade = dict(facade or {})
        self._tool_names = sorted(
            t.name for t in registry.all() if t.name != "codemode"
        )

    def __getitem__(self, name: str):
        return self._bind(name)

    def __getattr__(self, name: str):
        # Called only when normal attribute lookup failed — i.e. not for
        # _bind/_registry/etc. Dunder probes answer AttributeError so generic
        # protocol code (copy, pickle) keeps working.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return self._bind(name)

    def __dir__(self) -> list[str]:
        """The callable tool names, sorted — what ``dir(tools)`` shows.

        Registered tools only: the forgiven built-ins are reachable
        through the facade but never listed, same as ``all_tools()``.
        """
        return list(self._tool_names)

    def _resolve(self, name: str):
        """Resolve *name* to a registered tool name, or raise CodemodeError.

        One resolution for both entry points: the exact registered name,
        then the facade's built-ins. There is no second spelling — an MCP
        tool is reachable only under its full name — so a miss is an error
        naming the candidate full names, never a guess.
        """
        if name == "codemode":
            raise CodemodeError("codemode cannot be called from a script")
        if self._registry.get(name) is not None:
            return name
        if name in self._facade:
            return _FACADE
        raise CodemodeError(self._unknown_message(name))

    def _candidates(self, name: str) -> list[str]:
        """Registered names ending in the same ``__``-tail as *name* — the
        did-you-mean set when a call misses. Folded, so a hyphenated miss
        still finds the folded registered tail."""
        folded = normalize(name).rsplit("__", 1)[-1]
        return sorted(
            tool.name for tool in self._registry.all()
            if tool.name.rsplit("__", 1)[-1] == folded
        )

    def _unknown_message(self, name: str) -> str:
        base = f"unknown tool {name!r}; use search_tools() or all_tools()"
        found = self._candidates(name)
        if len(found) == 1:
            return f"{base} — did you mean {found[0]!r}?"
        if len(found) > 1:
            listed = ", ".join(repr(candidate) for candidate in found)
            return f"{base} — candidates: {listed}"
        return base

    def _bind(self, name: str):
        resolved = self._resolve(name)
        if resolved is _FACADE:
            return self._facade[name]
        return self._make_call(resolved)

    def _make_call(self, resolved: str):
        async def call(args: dict | None = None, **kwargs):
            merged = {**(args or {}), **kwargs}
            # A parallel() batch limit overrides the global cap: the batch
            # semaphore already bounds this call, so the box must not
            # acquire anything itself — stacking the two would halve the
            # effective limit (and, at a batch limit of one, deadlock).
            if _PARALLEL_LIMIT.get() is not None:
                semaphore = None
            else:
                semaphore = self._semaphore
            if semaphore is None:
                result = await self._dispatcher.run(
                    resolved,
                    merged,
                    origin="program",
                    parent_call_id=self._parent_call_id,
                )
            else:
                async with semaphore:
                    result = await self._dispatcher.run(
                        resolved,
                        merged,
                        origin="program",
                        parent_call_id=self._parent_call_id,
                    )
            self.calls += 1
            if result.status != "ok":
                raise ToolCallError(resolved, result)
            return Result(
                ok=True,
                content=result.content,
                details=result.details,
                tool=resolved,
                status=result.status,
                error_code=result.error_code,
            )

        return call


def tool_entries(registry: ToolRegistry) -> list[dict]:
    """The tools a script may call, as ``{"name", "description"}`` — the
    program-audience projection minus ``codemode``. This is a snapshot:
    tools registered after the script starts are not visible to it."""
    return [
        {"name": name, "description": registry.get(name).description}
        for name in registry.names(audience="program")
        if name != "codemode"
    ]


def describe_tool_entry(registry: ToolRegistry, name: str) -> dict | None:
    """A *callable* tool's ``{"name", "description", "schema"}`` — or None.

    Same source of truth as the callable surface: the program-audience
    projection minus ``codemode``. A name outside it — model-only,
    disabled, ``codemode`` itself, unregistered — describes as None
    instead of advertising something a script cannot call.
    """
    if name == "codemode":
        return None
    if name not in registry.names(audience="program"):
        return None
    tool = registry.get(name)
    return {"name": tool.name, "description": tool.description, "schema": tool.schema}

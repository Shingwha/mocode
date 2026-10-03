"""The script's ``tools`` facade — resolution, calls and the catalogue helpers.

:class:`ToolBox` is the ``tools`` name a script runs with: attribute and
subscript access bind one tool call through a three-tier resolution
(exact registered name, normalized name, unambiguous MCP short name), the
facade forgives the built-in names (``tools.describe_tool`` and friends
return the built-in itself), and ``codemode`` itself is never callable.
The catalogue helpers — :func:`tool_entries` and
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

#: The MCP naming prefix — the full name of an MCP tool is
#: ``mcp__<server>__<tool>`` (see ``mcp/naming.py``).
_MCP_PREFIX = "mcp__"

#: Sentinel :meth:`ToolBox._resolve` returns when a name is not a tool but
#: a built-in the facade forgives — :meth:`ToolBox._bind` then hands the
#: built-in itself back instead of a call.
_FACADE = object()


def _mcp_short_name(full: str) -> str | None:
    """The MCP short name of a registered tool name — ``mcp__k__bash`` →
    ``bash`` — or ``None`` for a name that is not MCP-style.

    The server segment may itself fold with ``__`` (a server raw-named
    ``a//b`` folds to ``a__b``), so the tool segment is taken with a right
    split: only the last ``__``-separated piece is the tool. A name with no
    ``__`` left after the prefix has no short form.
    """
    if not full.startswith(_MCP_PREFIX):
        return None
    rest = full[len(_MCP_PREFIX):]
    if "__" not in rest:
        return None
    short = rest.rsplit("__", 1)[1]
    return short or None


class ToolBox:
    """The script's ``tools`` — attribute/subscript access binds a tool call.

    Both entry points run the same resolution, in order: the exact
    registered name (``mcp__dev-radius__search``), its normalized form
    (``tools.mcp__dev_radius__search``; ``mcp__k__bash`` → ``tools.bash``
    and ``tools["mcp__k__bash"]`` therefore agree), and the MCP short name.
    The normalized and short forms only resolve when unambiguous — a
    collision raises instead of guessing, listing the candidates. When no
    tier matches, the facade forgives the built-in names (``store``,
    ``describe_tool``, …) and returns the built-in itself; anything else
    is unknown. ``codemode`` itself is never callable from a script. The
    bindings are a snapshot of the registry at :class:`ToolBox` creation,
    like ``all_tools()``.

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
        tools = [t for t in registry.all() if t.name != "codemode"]
        counts: dict[str, int] = {}
        for tool in tools:
            attr = normalize(tool.name)
            counts[attr] = counts.get(attr, 0) + 1
        self._attr_map = {
            attr: tool.name
            for tool in tools
            for attr in [normalize(tool.name)]
            if counts[attr] == 1
        }
        # The MCP short names — one per registered full name, kept only when
        # no second tool folds onto the same short name.
        shorts: dict[str, int] = {}
        for tool in tools:
            short = _mcp_short_name(tool.name)
            if short is not None:
                shorts[short] = shorts.get(short, 0) + 1
        self._short_map = {
            short: tool.name
            for tool in tools
            for short in [_mcp_short_name(tool.name)]
            if short is not None and shorts[short] == 1
        }
        #: Every registered name whose short form is *name* — the candidates
        #: an ambiguous short name reports.
        self._short_names: dict[str, list[str]] = {}
        for tool in tools:
            short = _mcp_short_name(tool.name)
            if short is not None:
                self._short_names.setdefault(short, []).append(tool.name)

    def __getitem__(self, name: str):
        return self._bind(name)

    def __getattr__(self, name: str):
        # Called only when normal attribute lookup failed — i.e. not for
        # _bind/_attr_map/etc. Dunder probes answer AttributeError so generic
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

        One resolution for both entry points: exact name, then normalized
        name, then MCP short name. A name that fails all three falls back
        to the facade's built-ins; when it collides as a short name the
        error lists the candidates (tools win over built-ins).
        """
        if name == "codemode":
            raise CodemodeError("codemode cannot be called from a script")
        if self._registry.get(name) is not None:
            return name
        target = self._attr_map.get(name)
        if target is not None:
            return target
        target = self._short_map.get(name)
        if target is not None:
            return target
        message = f"unknown tool {name!r}; use search_tools() or all_tools()"
        candidates = self._short_names.get(name, [])
        if len(candidates) > 1:
            listed = ", ".join(repr(c) for c in sorted(candidates))
            message += f" — ambiguous short name, candidates: {listed}"
            raise CodemodeError(message)
        if name in self._facade:
            return _FACADE
        raise CodemodeError(message)

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

"""The script-facing API — ToolBox, outcomes, discovery helpers, store, env.

Everything a codemode script may touch is assembled here into one globals
dict: the ``tools`` proxy, ``text``/``console``/``print``/``image``/``exit``,
the ``store``/``load`` closures, the discovery helpers and the read-only
standard-library modules. ``print`` is the one builtin the env replaces —
it appends to the output pipeline instead of writing to the host's stdout.

Every tool call a script makes goes through the dispatcher with
``origin="program"`` — that is the program-origin contract: the calls are
observable on the event stream, but they never enter ``messages`` and never
fold into the turn's ``tool_calls_made`` count.
"""

from __future__ import annotations

import asyncio
import collections
import datetime
import functools
import itertools
import json
import math
import re
import textwrap
from typing import TYPE_CHECKING, Any

from .....core.tool import ToolRegistry
from .result import Result, ToolCallError
from .runtime import CodemodeError, _ScriptExit
from .search import normalize, rank

if TYPE_CHECKING:
    from .....core.dispatch import ToolDispatcher
    from .output import Output

__all__ = [
    "Result",
    "Store",
    "ToolBox",
    "ToolCallError",
    "build_env",
    "describe_tool_entry",
    "tool_entries",
]

#: Injected read-only standard-library modules.
_MODULES = {
    "asyncio": asyncio,
    "json": json,
    "re": re,
    "math": math,
    "datetime": datetime,
    "textwrap": textwrap,
    "collections": collections,
    "itertools": itertools,
    "functools": functools,
}


#: The MCP naming prefix — the full name of an MCP tool is
#: ``mcp__<server>__<tool>`` (see ``mcp/naming.py``).
_MCP_PREFIX = "mcp__"


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

    Both entry points run the same resolution, in order: the exact registered
    name (``mcp__dev-radius__search``), its normalized form
    (``tools.mcp__dev_radius__search``; ``mcp__k__bash`` → ``tools.bash`` and
    ``tools["mcp__k__bash"]`` therefore agree), and the MCP short name. The
    normalized and short forms only resolve when unambiguous — a collision
    raises instead of guessing, listing the candidates. ``codemode`` itself
    is never callable from a script. The bindings are a snapshot of the
    registry at ``ToolBox`` creation, like ``all_tools()``.

    An optional ``semaphore`` caps how many of this box's calls run at once:
    every call acquires it around the dispatcher, so a fan-out queues
    instead of running all at once. ``None`` (the default) leaves calls
    unlimited.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: "ToolDispatcher",
        parent_call_id: str,
        semaphore: asyncio.Semaphore | None = None,
    ):
        self._registry = registry
        self._dispatcher = dispatcher
        self._parent_call_id = parent_call_id
        self._semaphore = semaphore
        #: Counts every ``dispatcher.run`` the box makes — reported as the
        #: result's ``tool_calls``.
        self.calls = 0
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

    def _resolve(self, name: str) -> str:
        """Resolve *name* to a registered tool name, or raise CodemodeError.

        One resolution for both entry points: exact name, then normalized
        name, then MCP short name. A name that fails all three is unknown;
        when it collides as a short name the error lists the candidates.
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

    def _bind(self, name: str):
        resolved = self._resolve(name)

        async def call(args: dict | None = None, **kwargs):
            merged = {**(args or {}), **kwargs}
            if self._semaphore is None:
                result = await self._dispatcher.run(
                    resolved,
                    merged,
                    origin="program",
                    parent_call_id=self._parent_call_id,
                )
            else:
                async with self._semaphore:
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


class _Console:
    """The script's ``console`` — every level appends one text item."""

    def __init__(self, emit):
        self._emit = emit

    def log(self, *args):
        self._emit(" ".join(map(str, args)))

    def info(self, *args):
        self._emit(" ".join(map(str, args)))

    def warn(self, *args):
        self._emit(" ".join(map(str, args)))

    def error(self, *args):
        self._emit(" ".join(map(str, args)))

    def debug(self, *args):
        self._emit(" ".join(map(str, args)))


#: Sentinel marking a pending deletion — ``store(key, None)``.
_DELETED = object()

#: Defaults mirroring the ``plugins.codemode`` config keys.
DEFAULT_STORE_MAX_VALUE_CHARS = 262144
DEFAULT_STORE_MAX_TOTAL_CHARS = 1048576


class Store:
    """Small JSON state shared across a conversation's codemode calls.

    Writes land in an overlay; :meth:`commit` applies them to the backing
    dict (the plugin's session-persisted state slot) only after the script
    succeeded — a failing script discards everything pending. Limits are
    checked at commit time: no pending write is applied when any check
    fails, and the check covers the store as it would stand after the
    commit, so the persisted session stays small.
    """

    def __init__(
        self,
        backing: dict,
        *,
        max_value_chars: int = DEFAULT_STORE_MAX_VALUE_CHARS,
        max_total_chars: int = DEFAULT_STORE_MAX_TOTAL_CHARS,
    ):
        self._backing = backing
        self._pending: dict = {}
        self._max_value_chars = max_value_chars
        self._max_total_chars = max_total_chars

    def store(self, key: str, value: Any) -> None:
        """Queue *value* under *key*; ``None`` deletes the key on commit."""
        self._pending[key] = _DELETED if value is None else value

    def load(self, key: str) -> Any:
        """The pending value when one is queued, else the backing value."""
        if key in self._pending:
            value = self._pending[key]
            return None if value is _DELETED else value
        return self._backing.get(key)

    def commit(self) -> None:
        """Validate the limits and apply pending writes to the backing dict.

        Raises :class:`CodemodeError` without applying anything when a
        single value or the resulting store exceeds its limit.
        """
        staged = dict(self._backing)
        for key, value in self._pending.items():
            if value is _DELETED:
                staged.pop(key, None)
            else:
                staged[key] = value
        for key, value in staged.items():
            size = len(json.dumps(value, default=str))
            if size > self._max_value_chars:
                raise CodemodeError(
                    f"stored value for {key!r} is {size} chars of JSON, "
                    f"over the {self._max_value_chars} limit"
                )
        total = sum(len(json.dumps(v, default=str)) for v in staged.values())
        if total > self._max_total_chars:
            raise CodemodeError(
                f"stored values total {total} chars of JSON, "
                f"over the {self._max_total_chars} limit"
            )
        self._backing.update(staged)
        for key, value in self._pending.items():
            if value is _DELETED:
                self._backing.pop(key, None)
        self._pending.clear()


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


def _render_argument(value: Any) -> str:
    """One argument's text: strings as-is, everything else as JSON.

    The convention :class:`~mocode.host.plugin.builtin.codemode.output.Output`
    applies to an item, here applied per argument so ``print({"a": 1})``
    lands as ``{"a": 1}`` rather than Python's repr.
    """
    return value if isinstance(value, str) else json.dumps(value, default=str)


def build_env(
    registry: ToolRegistry,
    dispatcher: "ToolDispatcher",
    parent_call_id: str,
    output: "Output",
    store: Store,
    *,
    max_concurrency: int | None = None,
) -> tuple[dict, ToolBox]:
    """Assemble the globals dict a script runs with.

    Returns the env and the :class:`ToolBox` (for its call counter). The
    discovery surface — ``all_tools()`` and ``search_tools()`` — reads one
    snapshot computed here, at script start. ``max_concurrency`` caps how
    many of the script's tool calls run at once through one per-script
    semaphore; ``None`` (the default) leaves the calls unlimited.
    """
    semaphore = (
        asyncio.Semaphore(max_concurrency) if max_concurrency is not None else None
    )
    toolbox = ToolBox(registry, dispatcher, parent_call_id, semaphore=semaphore)
    entries = tool_entries(registry)
    console = _Console(output.text)

    def text(value: Any) -> None:
        output.text(value)

    def script_print(*args, sep: str = " ") -> None:
        """The script's ``print`` — one output item, like ``console.log``.

        Non-string arguments are JSON-ified exactly as ``text()`` renders
        them. ``end`` is deliberately not offered: an item is one line and
        the renderer already joins items with newlines, so a trailing
        newline would double up — a script that needs that emits it with
        ``text()``.
        """
        output.text(sep.join(_render_argument(arg) for arg in args))

    def image(block: Any) -> None:
        output.image(block)

    def exit() -> None:
        raise _ScriptExit()

    def store_value(key: str, value: Any) -> None:
        store.store(key, value)

    def load_value(key: str) -> Any:
        return store.load(key)

    def all_tools() -> list[dict]:
        """The startup snapshot: the program-audience tools as
        ``{"name", "description"}`` entries, minus ``codemode``."""
        return list(entries)

    def search_tools(
        query: str,
        limit: int = 8,
        namespace: str | None = None,
        names_only: bool = False,
    ) -> list[dict] | list[str]:
        """Rank the snapshot against *query* — entries, or just their names
        with ``names_only=True``."""
        hits = rank(query, entries, limit=limit, namespace=namespace)
        if names_only:
            return [hit["name"] for hit in hits]
        return hits

    env = dict(_MODULES)
    env.update(
        {
            "tools": toolbox,
            "text": text,
            "console": console,
            "print": script_print,
            "image": image,
            "exit": exit,
            "store": store_value,
            "load": load_value,
            "all_tools": all_tools,
            "search_tools": search_tools,
            "describe_tool": lambda name: describe_tool_entry(registry, name),
        }
    )
    return env, toolbox

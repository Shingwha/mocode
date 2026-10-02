"""The script-facing API — ToolBox, outcomes, discovery helpers, store, env.

Everything a codemode script may touch is assembled here into one globals
dict: the ``tools`` proxy, ``text``/``console``/``image``/``exit``, the
``store``/``load`` closures, the discovery helpers and the read-only
standard-library modules.

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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .....core.tool import ToolRegistry
from .runtime import CodemodeError, _ScriptExit
from .search import normalize, rank

if TYPE_CHECKING:
    from .....core.dispatch import DispatchResult, ToolDispatcher
    from .output import Output

__all__ = [
    "Store",
    "ToolBox",
    "ToolCallError",
    "ToolOutcome",
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


class ToolCallError(Exception):
    """A script's tool call came back with a non-ok status.

    ``str()`` is ``"<name>: <content>"`` — the line a script logs when it
    catches the failure — and ``.result`` is the raw
    :class:`DispatchResult`, so scripts (and tests) can inspect the status,
    details and error code.
    """

    def __init__(self, name: str, result: "DispatchResult"):
        self.name = name
        self.result = result
        super().__init__(f"{name}: {result.content}")


@dataclass
class ToolOutcome:
    """What a successful ``tools.<name>(...)`` returns inside a script."""

    content: str
    details: dict = field(default_factory=dict)
    status: str = "ok"
    error_code: str | None = None

    def __str__(self) -> str:
        return self.content

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "details": self.details,
            "status": self.status,
            "error_code": self.error_code,
        }


class ToolBox:
    """The script's ``tools`` — attribute/subscript access binds a tool call.

    ``tools.echo({"value": "x"})`` and ``tools["echo"](value="x")`` both work;
    a name that is not a valid Python identifier is reachable through its
    normalized form (``mcp__dev-radius__search`` → ``tools.mcp__dev_radius__search``)
    when that normalization is unambiguous. ``codemode`` itself is never
    callable from a script. Bindings are snapshots of the registry at
    ``ToolBox`` creation, like ``ALL_TOOLS``.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: "ToolDispatcher",
        parent_call_id: str,
    ):
        self._registry = registry
        self._dispatcher = dispatcher
        self._parent_call_id = parent_call_id
        #: Counts every ``dispatcher.run`` the box makes — reported as the
        #: result's ``tool_calls``.
        self.calls = 0
        counts: dict[str, int] = {}
        for tool in registry.all():
            if tool.name == "codemode":
                continue
            attr = normalize(tool.name)
            counts[attr] = counts.get(attr, 0) + 1
        self._attr_map = {
            attr: tool.name
            for tool in registry.all()
            if tool.name != "codemode"
            for attr in [normalize(tool.name)]
            if counts[attr] == 1
        }

    def __getitem__(self, name: str):
        return self._bind(name)

    def __getattr__(self, name: str):
        # Called only when normal attribute lookup failed — i.e. not for
        # _bind/_attr_map/etc. Dunder probes answer AttributeError so generic
        # protocol code (copy, pickle) keeps working.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if self._registry.get(name) is not None:
            return self._bind(name)
        target = self._attr_map.get(name)
        if target is not None:
            return self._bind(target)
        raise CodemodeError(
            f"unknown tool {name!r}; use search_tools() or ALL_TOOLS"
        )

    def _bind(self, name: str):
        """Resolve *name* to an async callable, or raise CodemodeError."""
        if name == "codemode":
            raise CodemodeError("codemode cannot be called from a script")
        if self._registry.get(name) is None:
            raise CodemodeError(
                f"unknown tool {name!r}; use search_tools() or ALL_TOOLS"
            )

        async def call(args: dict | None = None, **kwargs):
            merged = {**(args or {}), **kwargs}
            result = await self._dispatcher.run(
                name,
                merged,
                origin="program",
                parent_call_id=self._parent_call_id,
            )
            self.calls += 1
            if result.status != "ok":
                raise ToolCallError(name, result)
            return ToolOutcome(
                content=result.content,
                details=result.details,
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
    """A registered tool's ``{"name", "description", "schema"}`` — or None."""
    tool = registry.get(name)
    if tool is None:
        return None
    return {"name": tool.name, "description": tool.description, "schema": tool.schema}


def build_env(
    registry: ToolRegistry,
    dispatcher: "ToolDispatcher",
    parent_call_id: str,
    output: "Output",
    store: Store,
) -> tuple[dict, ToolBox]:
    """Assemble the globals dict a script runs with.

    Returns the env and the :class:`ToolBox` (for its call counter). The
    discovery snapshots — ``ALL_TOOLS`` and ``search_tools`` — are computed
    here, once, at script start.
    """
    toolbox = ToolBox(registry, dispatcher, parent_call_id)
    entries = tool_entries(registry)
    console = _Console(output.text)

    def text(value: Any) -> None:
        output.text(value)

    def image(block: Any) -> None:
        output.image(block)

    def exit() -> None:
        raise _ScriptExit()

    def store_value(key: str, value: Any) -> None:
        store.store(key, value)

    def load_value(key: str) -> Any:
        return store.load(key)

    def search_tools(
        query: str, limit: int = 8, namespace: str | None = None
    ) -> list[dict]:
        return rank(query, entries, limit=limit, namespace=namespace)

    env = dict(_MODULES)
    env.update(
        {
            "tools": toolbox,
            "text": text,
            "console": console,
            "image": image,
            "exit": exit,
            "store": store_value,
            "load": load_value,
            "ALL_TOOLS": entries,
            "search_tools": search_tools,
            "describe_tool": lambda name: describe_tool_entry(registry, name),
        }
    )
    return env, toolbox

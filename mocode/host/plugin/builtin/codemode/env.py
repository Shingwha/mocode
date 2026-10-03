"""The script's globals — every name a codemode script may touch, assembled.

:func:`build_env` puts the env dict together: the ``tools`` proxy, the
output pipeline names (``text``/``console``/``print``/``image``/``exit``),
the ``store``/``load`` closures, ``parallel``, the discovery helpers and
the read-only standard-library modules. The built-in functions form the
facade :class:`~mocode.host.plugin.builtin.codemode.toolbox.ToolBox`
forgives: ``tools.describe_tool`` and friends resolve to the very same
objects the bare names bind to. ``print`` is the one builtin the env
replaces — it appends to the output pipeline instead of writing to the
host's stdout.
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
from .result import parallel
from .runtime import _ScriptExit
from .search import preview, rank
from .store import Store
from .toolbox import ToolBox, describe_tool_entry, tool_entries

if TYPE_CHECKING:
    from .....core.dispatch import ToolDispatcher
    from .output import Output

__all__ = ["build_env"]

#: Injected read-only standard-library modules. The key set mirrors
#: ``runtime.IMPORT_WHITELIST`` — the gate a script's ``import`` statement
#: passes through — and a test keeps the two in lockstep.
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
        """The startup snapshot: the program-audience tools as catalogue
        entries, minus ``codemode`` — descriptions previewed at the
        catalogue trim, full text via ``describe_tool(name)``."""
        return [preview(entry) for entry in entries]

    def search_tools(
        query: str,
        limit: int = 8,
        namespace: str | None = None,
        names_only: bool = False,
    ) -> list[dict] | list[str]:
        """Rank the snapshot against *query* — entries, or just their names
        with ``names_only=True``. Ranking reads the full descriptions; the
        catalogue entries are previewed at the return boundary."""
        hits = rank(query, entries, limit=limit, namespace=namespace)
        if names_only:
            return [hit["name"] for hit in hits]
        return [preview(hit) for hit in hits]

    def describe(name: str) -> dict | None:
        return describe_tool_entry(registry, name)

    toolbox = ToolBox(
        registry,
        dispatcher,
        parent_call_id,
        semaphore=semaphore,
        facade={
            "describe_tool": describe,
            "all_tools": all_tools,
            "search_tools": search_tools,
            "store": store_value,
            "load": load_value,
            "text": text,
            "console": console,
            "image": image,
            "print": script_print,
            "exit": exit,
        },
    )

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
            "parallel": parallel,
            "all_tools": all_tools,
            "search_tools": search_tools,
            "describe_tool": describe,
        }
    )
    return env, toolbox

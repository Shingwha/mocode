"""The cross-call store — small JSON state shared by a conversation's scripts.

The plugin owns one Store per codemode call, backed by the plugin's
session-persisted state slot. Writes land in an overlay; :meth:`commit`
applies them to the backing dict only after the script succeeded — a
failing script discards everything pending. Limits are checked at commit
time in JSON-serialized characters, so the persisted session stays small.
"""

from __future__ import annotations

import json
from typing import Any

from .runtime import CodemodeError

__all__ = [
    "DEFAULT_STORE_MAX_TOTAL_CHARS",
    "DEFAULT_STORE_MAX_VALUE_CHARS",
    "Store",
]

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

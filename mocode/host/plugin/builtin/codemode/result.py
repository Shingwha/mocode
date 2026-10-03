"""Tool results — the first-class value a script's calls produce.

Every ``tools.<name>(...)`` returns a :class:`Result`: the four wire fields
(``content`` / ``details`` / ``status`` / ``error_code``) plus the
first-class surface — ``ok``, ``tool``, ``error``, the ``json()`` /
``structured`` helpers and a readable ``repr`` — and a Mapping protocol over
the wire fields, so ``res.get("content")`` works alongside ``res.content``.

Failure policy (deliberately asymmetric): a single awaited call raises
:class:`ToolCallError` fast; :func:`parallel` is the one place failures come
back as data — a failed :class:`Result` in ``batch.failed``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .....core.dispatch import DispatchResult

__all__ = ["Result", "ToolCallError"]

#: The four wire keys of :class:`Result` the Mapping protocol answers, in
#: field order.
_RESULT_FIELDS = ("content", "details", "status", "error_code")


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


def _size_label(chars: int) -> str:
    """A readable content size for reprs — ``512 chars`` or ``3.1k chars``."""
    if chars < 1000:
        return f"{chars} chars"
    return f"{chars / 1000:.1f}k chars"


@dataclass
class Result:
    """What a successful ``tools.<name>(...)`` returns inside a script.

    The four wire fields stay attributes; ``ok`` mirrors the outcome,
    ``tool`` is the resolved registered name, and a failed result (only ever
    built by :func:`parallel`) carries the human-readable ``error``. The
    object also answers the Mapping protocol — :meth:`get`,
    :meth:`__getitem__`, :meth:`keys`, :meth:`__contains__` — so
    ``res["content"]`` / ``res.get("details")`` / ``dict(res)`` work
    alongside ``res.content``.
    """

    ok: bool = True
    content: str = ""
    details: dict = field(default_factory=dict)
    tool: str = ""
    status: str = "ok"
    error_code: str | None = None
    error: str | None = None

    def __str__(self) -> str:
        return self.content

    def __repr__(self) -> str:
        if not self.ok:
            reason = (self.error or self.content or "?")[:60]
            return f"<Result error {self.tool or '?'}: {reason}>"
        return f"<Result ok {self.tool or '?'} {_size_label(len(self.content))}>"

    @property
    def structured(self):
        """The MCP tool's ``structuredContent``, when the tool returned one
        (surfaced as ``details["structured_content"]``); ``None`` otherwise.
        """
        return self.details.get("structured_content")

    def json(self):
        """``content`` parsed as JSON.

        Returns the parsed value on success; on failure returns — never
        raises — a short string carrying the parse error and a snippet of
        the content, so a script can print it and see what it actually got.
        """
        try:
            return json.loads(self.content)
        except ValueError as e:
            snippet = self.content[:200]
            return f"content is not valid JSON: {e}; content starts: {snippet!r}"

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "details": self.details,
            "status": self.status,
            "error_code": self.error_code,
        }

    def __getitem__(self, key: str):
        if key not in _RESULT_FIELDS:
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key) if key in _RESULT_FIELDS else default

    def keys(self) -> list[str]:
        return list(_RESULT_FIELDS)

    def __contains__(self, key) -> bool:
        return key in _RESULT_FIELDS

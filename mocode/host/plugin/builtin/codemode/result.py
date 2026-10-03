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

import asyncio
import inspect
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .....core.dispatch import DispatchResult

__all__ = ["Batch", "Result", "ToolCallError", "parallel"]

#: The per-batch concurrency limit, set while :func:`parallel` runs its
#: calls. ToolBox reads it instead of the global max_concurrency semaphore:
#: a per-batch ``concurrency`` overrides the global cap for its calls.
_PARALLEL_LIMIT: ContextVar[asyncio.Semaphore | None] = ContextVar(
    "codemode_parallel_limit", default=None
)

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


class Batch(list):
    """What :func:`parallel` returns: the results in argument order.

    A plain list in every other respect — ``len`` / indexing / iteration —
    with :attr:`ok` and :attr:`failed` splitting the results without
    losing the order either way.
    """

    @property
    def ok(self) -> list[Result]:
        """The successful results, in argument order."""
        return [r for r in self if r.ok]

    @property
    def failed(self) -> list[Result]:
        """The failed results (``.error`` carries the reason), in order."""
        return [r for r in self if not r.ok]


def _failure_result(exc: BaseException) -> Result:
    """The ``ok=False`` Result a captured per-call failure becomes."""
    if isinstance(exc, ToolCallError):
        result = exc.result
        return Result(
            ok=False,
            content=result.content,
            details=result.details,
            tool=exc.name,
            status=result.status,
            error_code=result.error_code,
            error=str(exc),
        )
    return Result(ok=False, status="error", error=f"{type(exc).__name__}: {exc}")


async def _capture(call) -> Result:
    """Await one call, turning a failure into data (never into a raise).

    Cancellation is the one exception to the capture: it stays contractual
    and propagates untouched, or a stopped script would report a wrong
    outcome.
    """
    try:
        value = await call
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _failure_result(exc)
    if isinstance(value, Result):
        return value
    return Result(ok=True, content=value if isinstance(value, str) else json.dumps(value, default=str))


def _reject(calls: tuple, error: Exception) -> None:
    """Raise *error* for invalid parallel() arguments, closing the coroutine
    arguments so a rejected batch leaves no orphaned, never-awaited coroutine."""
    for call in calls:
        if inspect.iscoroutine(call):
            call.close()
    raise error


async def parallel(*calls, concurrency: int | None = None) -> Batch:
    """Run every call concurrently and come back with all their results.

    Each argument is one awaitable — typically a ``tools.<name>(...)``
    call. The returned :class:`Batch` holds one :class:`Result` per call,
    in argument order; a call that fails never sinks the batch, it lands
    in ``batch.failed`` with ``.error`` carrying the reason. Only
    argument errors raise: a non-awaitable, or a ``concurrency`` that is
    not a positive integer.

    ``concurrency`` caps how many of the batch's calls run at once and
    overrides the global ``plugins.codemode.max_concurrency`` for its
    calls; ``None`` (the default) leaves them under the global cap alone.
    """
    if concurrency is not None and (
        isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or concurrency <= 0
    ):
        _reject(calls, ValueError(
            f"parallel() concurrency must be a positive integer or None, got {concurrency!r}"
        ))
    for call in calls:
        if not inspect.isawaitable(call):
            _reject(calls, TypeError(
                "parallel() expects awaitables such as tools.<name>(...) coroutines, "
                f"got {call!r}"
            ))
    semaphore = asyncio.Semaphore(concurrency) if concurrency is not None else None

    async def run_one(call):
        # The batch semaphore bounds every call in the batch; ToolBox sees
        # the same semaphore through _PARALLEL_LIMIT and skips the global
        # cap, so the two never stack up (and never deadlock).
        async with semaphore:
            return await _capture(call)

    if semaphore is None:
        results = await asyncio.gather(*(_capture(c) for c in calls))
    else:
        token = _PARALLEL_LIMIT.set(semaphore)
        try:
            results = await asyncio.gather(*(run_one(c) for c in calls))
        finally:
            _PARALLEL_LIMIT.reset(token)
    return Batch(results)

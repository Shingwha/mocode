"""Agent events — the kernel's output contract.

One turn emits one ordered stream of these. Every consumer — the terminal
renderer, a plugin, an embedding application — is a projection of the same
stream, and every event is plain data with :meth:`Event.to_dict` so it can
cross a process boundary unchanged.

Two rules keep the contract usable:

* ``run_id`` + ``seq`` identify and order events. A consumer that fans them out
  to several places can re-order or drop duplicates without guessing.
* A tool call is an object with an identity, not a return value: it starts,
  may report output while it runs, and finishes with a status. All three
  events carry the same ``call_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, ClassVar

from .provider import Usage


def _plain(value: Any) -> Any:
    """Recursively convert dataclasses/containers into JSON-ready data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


@dataclass(kw_only=True)
class Event:
    """Base class for everything the loop emits.

    ``run_id`` / ``seq`` are keyword-only so subclasses read positionally:
    ``TextDelta("hi")`` sets ``text``, not ``run_id``. Subclasses must declare a
    unique ``type`` — it is the discriminator a transport uses to rebuild the
    event on the other side.
    """

    run_id: str = ""
    seq: int = 0
    type: ClassVar[str] = "event"

    def _payload(self) -> list:
        """The declared fields, minus the envelope every event carries."""
        return [f for f in fields(self) if f.name not in ("run_id", "seq")]

    def to_dict(self) -> dict[str, Any]:
        """Flatten to plain data: ``{"type", "run_id", "seq", **fields}``."""
        data: dict[str, Any] = {
            "type": self.type,
            "run_id": self.run_id,
            "seq": self.seq,
        }
        for f in self._payload():
            data[f.name] = _plain(getattr(self, f.name))
        return data

    def summary(self) -> str:
        """One line a frontend can show in place of the raw event.

        Override it on an event of your own — then any frontend, including one
        written after yours, can display it without knowing the type. Keep it
        short; a frontend may fall back to ``to_dict()`` when it wants detail.
        """
        parts = [f"{f.name}={getattr(self, f.name)!r}" for f in self._payload()]
        label = type(self).__name__
        return f"{label}: {', '.join(parts)}" if parts else label

    def __str__(self) -> str:
        return self.type


# ---- Run lifecycle ----


@dataclass
class RunStarted(Event):
    """A turn began. ``tools`` is the tool set visible to the model this run."""

    model: str = ""
    tools: list[str] = field(default_factory=list)
    type: ClassVar[str] = "run_started"


@dataclass
class RunFinished(Event):
    """A turn ended normally.

    ``content`` is the final answer — the text of the last iteration, which is
    what a caller wants as "the reply". Text streamed during earlier iterations
    was commentary around tool calls; a consumer that rendered the deltas has
    already shown it.

    ``cancelled`` marks a turn that was stopped rather than completed: it is
    still a normal ending (the terminal event of that turn), and ``content`` is
    empty because no iteration produced an answer. What had streamed so far is
    in ``RunState.content``.
    """

    content: str = ""
    usage: Usage | None = None
    iterations: int = 0
    tool_calls_made: int = 0
    had_error: bool = False
    cancelled: bool = False
    type: ClassVar[str] = "run_finished"


@dataclass
class RunFailed(Event):
    """The turn ended on an unhandled exception."""

    error: str = ""
    kind: str = ""
    type: ClassVar[str] = "run_failed"


# ---- Iterations ----


@dataclass
class IterationStarted(Event):
    """One LLM call is about to be made."""

    iteration: int = 0
    type: ClassVar[str] = "iteration_started"


@dataclass
class IterationFinished(Event):
    """One LLM response has been fully received."""

    iteration: int = 0
    usage: Usage | None = None
    stop_reason: str | None = None
    type: ClassVar[str] = "iteration_finished"


# ---- Model output ----


@dataclass
class TextDelta(Event):
    """A fragment of assistant text. Concatenate in ``seq`` order."""

    text: str = ""
    type: ClassVar[str] = "text_delta"


@dataclass
class ReasoningDelta(Event):
    """A fragment of the model's reasoning trace, when the model exposes one."""

    text: str = ""
    type: ClassVar[str] = "reasoning_delta"


# ---- Tool calls ----


@dataclass
class ToolCallStarted(Event):
    """A tool is about to execute.

    Emitted *after* ``on_tool_start`` hooks ran, so ``args`` are final. Fires
    for denied calls too — the denial is reported by the matching
    :class:`ToolCallFinished`.
    """

    call_id: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    type: ClassVar[str] = "tool_call_started"


@dataclass
class ToolOutput(Event):
    """Incremental output from a running tool.

    ``stream`` is ``"stdout"`` or ``"stderr"``. Only tools that opt into
    streaming report these; the rest go straight from started to finished.
    """

    call_id: str = ""
    text: str = ""
    stream: str = "stdout"
    type: ClassVar[str] = "tool_output"


@dataclass
class ToolCallFinished(Event):
    """A tool finished, was denied, timed out or failed.

    ``status`` is one of ``ok`` / ``error`` / ``timeout`` / ``denied`` /
    ``not_found``; ``result`` is what the model will receive.

    ``details`` is whatever structured data the tool attached to its result —
    the model never sees it, a frontend or an application can.
    """

    call_id: str = ""
    name: str = ""
    status: str = "ok"
    result: str = ""
    error_code: str | None = None
    duration: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    type: ClassVar[str] = "tool_call_finished"

    def summary(self) -> str:
        return f"{self.name} [{self.status}]"


# ---- Plugin channel ----


@dataclass
class Notice(Event):
    """A message a plugin wants the host to show. ``level`` is informational."""

    message: str = ""
    level: str = "info"
    type: ClassVar[str] = "notice"


__all__ = [
    "Event",
    "IterationFinished",
    "IterationStarted",
    "Notice",
    "ReasoningDelta",
    "RunFailed",
    "RunFinished",
    "RunStarted",
    "TextDelta",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolOutput",
]

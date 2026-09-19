"""RunState — a live snapshot of a turn, reduced from its event stream.

The state is a pure function of the events: feed every event to
:meth:`RunState.apply` and read the object at any moment to answer "what is
happening right now". Nothing here reaches back into the loop, so an embedding
application can hold its own reducer over the same stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import (
    Event,
    IterationFinished,
    IterationStarted,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    TextDelta,
    TOOL_OK,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
)
from .provider import Usage

#: ``RunState.status`` values.
IDLE = "idle"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

#: Fold-only tool status: a call that has started and not finished. Never an
#: event status — the wire carries only the five terminal ``TOOL_*`` values
#: defined in :mod:`mocode.core.events`.
TOOL_RUNNING = "running"


@dataclass
class ToolCallState:
    """One tool call as observed so far.

    ``status`` is :data:`TOOL_RUNNING <mocode.core.state.TOOL_RUNNING>` until
    the call finishes, then one of the terminal ``TOOL_*`` values from
    :mod:`mocode.core.events`.
    """

    call_id: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    status: str = TOOL_RUNNING
    result: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    duration: float = 0.0
    output: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.status != TOOL_RUNNING

    @property
    def output_text(self) -> str:
        """Everything the tool streamed while it ran."""
        return "".join(self.output)

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "args": self.args,
            "status": self.status,
            "result": self.result,
            "details": self.details,
            "error_code": self.error_code,
            "duration": self.duration,
            "output": self.output_text,
        }


@dataclass
class RunState:
    """Live snapshot of a turn.

    ``content`` is everything streamed this turn, including the commentary the
    model wraps around tool calls. ``answer`` is the last iteration's text —
    the reply itself, and what ``chat()`` returns.

    Folding is guarded, so the same events may be applied more than once —
    a reconnect replaying a range the reader had already folded — and events
    from another run sharing the channel are ignored. Each ``RunState`` is one
    run's view, not the channel's.
    """

    run_id: str = ""
    status: str = IDLE
    model: str = ""
    iteration: int = 0
    content: str = ""
    answer: str = ""
    reasoning: str = ""
    tool_calls: dict[str, ToolCallState] = field(default_factory=dict)
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    last_usage: Usage | None = None
    error: str = ""
    #: Highest stamped ``seq`` folded so far. An unstamped event (``seq == 0``)
    #: is one the loop folds *before* the channel stamps it — those always
    #: pass, so the live path keeps its ordering; replayed events, already
    #: stamped, are deduplicated here instead.
    _last_seq: int = field(default=0, repr=False)

    def apply(self, event: Event) -> None:
        """Fold one event into the snapshot. Unknown events are ignored."""
        if event.seq and event.seq <= self._last_seq:
            return  # already folded — an overlapping replay
        if self.run_id and event.run_id and event.run_id != self.run_id:
            return  # another run sharing this channel
        self._last_seq = max(self._last_seq, event.seq)
        match event:
            case RunStarted():
                self.run_id = event.run_id
                self.status = RUNNING
                self.model = event.model
            case IterationStarted():
                self.iteration = event.iteration
            case TextDelta():
                self.content += event.text
            case ReasoningDelta():
                self.reasoning += event.text
            case ToolCallStarted():
                self.tool_calls[event.call_id] = ToolCallState(
                    call_id=event.call_id,
                    name=event.name,
                    args=dict(event.args),
                )
            case ToolOutput():
                call = self.tool_calls.get(event.call_id)
                if call is not None:
                    call.output.append(event.text)
            case ToolCallFinished():
                call = self.tool_calls.get(event.call_id)
                if call is not None:
                    call.status = event.status
                    call.result = event.result
                    call.details = dict(event.details)
                    call.error_code = event.error_code
                    call.duration = event.duration
            case IterationFinished():
                self.last_usage = event.usage
                if event.usage is not None:
                    self.usage = Usage(
                        self.usage.prompt_tokens + event.usage.prompt_tokens,
                        self.usage.completion_tokens + event.usage.completion_tokens,
                    )
            case RunFinished():
                self.status = CANCELLED if event.cancelled else DONE
                self.iteration = event.iterations or self.iteration
                self.answer = event.content
                # This event is the run's own summary, so its totals are
                # authoritative — a consumer that also saw every
                # IterationFinished lands on the same number either way.
                if event.usage is not None:
                    self.usage = Usage(
                        event.usage.prompt_tokens, event.usage.completion_tokens
                    )
            case RunFailed():
                self.status = FAILED
                self.error = event.error

    # ---- Queries ----

    @property
    def running_tool_calls(self) -> list[ToolCallState]:
        """Tool calls that have started and not yet finished."""
        return [c for c in self.tool_calls.values() if not c.done]

    @property
    def tool_calls_made(self) -> int:
        """Tool calls started this turn, including denied and failed ones."""
        return len(self.tool_calls)

    @property
    def failed_tool_calls(self) -> list[ToolCallState]:
        return [
            c for c in self.tool_calls.values() if c.status not in (TOOL_OK, TOOL_RUNNING)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "model": self.model,
            "iteration": self.iteration,
            "content": self.content,
            "answer": self.answer,
            "reasoning": self.reasoning,
            "tool_calls": {cid: c.to_dict() for cid, c in self.tool_calls.items()},
            "usage": self.usage.to_dict(),
            "last_usage": (
                None if self.last_usage is None else self.last_usage.to_dict()
            ),
            "error": self.error,
        }


__all__ = [
    "CANCELLED",
    "DONE",
    "FAILED",
    "IDLE",
    "RUNNING",
    "RunState",
    "ToolCallState",
]

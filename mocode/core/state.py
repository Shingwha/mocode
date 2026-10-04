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
    ToolCallArgsDelta,
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

#: Fold-only tool statuses: a call the model is still writing the arguments
#: for, and one that is executing. Neither is an event status — the wire
#: carries only the five terminal ``TOOL_*`` values defined in
#: :mod:`mocode.core.events`.
TOOL_FORMING = "forming"
TOOL_RUNNING = "running"


@dataclass
class ToolCallState:
    """One tool call as observed so far.

    ``status`` is :data:`TOOL_FORMING <mocode.core.state.TOOL_FORMING>` while
    the model streams the arguments, :data:`TOOL_RUNNING
    <mocode.core.state.TOOL_RUNNING>` once the call is announced for
    execution, then one of the terminal ``TOOL_*`` values from
    :mod:`mocode.core.events`.
    """

    call_id: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    #: The argument text as it streamed in, before the call was announced for
    #: execution. Fold-only live view: not part of ``to_dict`` — a call that is
    #: running (or finished) shows its parsed ``args`` instead, and a forming
    #: call cannot outlive the turn it is in.
    args_text: str = ""
    status: str = TOOL_RUNNING
    result: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    duration: float = 0.0
    output: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        """Whether this call has finished — any terminal status, including an error.

        The opposite of the two fold-only statuses: while it is False the call
        is still in flight — forming or running — and its result is not yet an
        answer.
        """
        return self.status not in (TOOL_RUNNING, TOOL_FORMING)

    @property
    def output_text(self) -> str:
        """Everything the tool streamed while it ran."""
        return "".join(self.output)

    def to_dict(self) -> dict[str, Any]:
        """This call as plain data — the shape a session stores it in.

        ``output`` is folded into one string, because the streaming cadence is
        a display concern and not a fact about the call.
        """
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

    ``content`` has a soft ceiling (``content_limit``, default 200k
    characters): past it the newest text is kept and the front replaced by a
    marker saying how much came before — a valve against a runaway turn
    growing the snapshot without bound, in the same spirit as
    ``AgentConfig.tool_result_limit``. ``answer`` is never elided: it comes
    from the terminal event, not from this accumulation.

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
    #: Characters of streamed text ``content`` keeps once past the soft
    #: limit. ``0`` disables the ceiling. Constructor policy only — a
    #: loop-internal valve, deliberately not a config key.
    content_limit: int = 200_000
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
                self._elide_content()
            case ReasoningDelta():
                self.reasoning += event.text
            case ToolCallArgsDelta():
                call = self.tool_calls.get(event.call_id)
                if call is None:
                    call = self.tool_calls.setdefault(
                        event.call_id,
                        ToolCallState(
                            call_id=event.call_id, name=event.name, status=TOOL_FORMING
                        ),
                    )
                if event.name:
                    call.name = event.name
                call.args_text += event.arguments
            case ToolCallStarted():
                call = self.tool_calls.get(event.call_id)
                if call is None:
                    call = ToolCallState(call_id=event.call_id, name=event.name)
                    self.tool_calls[event.call_id] = call
                else:
                    call.name = event.name or call.name
                call.status = TOOL_RUNNING
                call.args = dict(event.args)
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

    def _elide_content(self) -> None:
        """Past the soft limit, keep the newest text and mark the elision.

        The snapshot stays a ``str`` and readers stay unaware — only the head
        of the window says that text was dropped, and how much. The count is
        derived from the trimmed string, so a second elision counts the first
        marker too: a few characters of drift on a valve, not a ledger.
        """
        if not self.content_limit or len(self.content) <= self.content_limit:
            return
        total = len(self.content)
        window = self.content[-self.content_limit :]
        self.content = f"…[{total - len(window)} chars elided]\n" + window

    @property
    def tool_calls_made(self) -> int:
        """Tool calls started this turn, including denied and failed ones."""
        return len(self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        """The snapshot as plain data — the shape a session stores it in.

        Mirrors the folded events, so a resumed session reconstructs the same
        snapshot without replaying the stream.
        """
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

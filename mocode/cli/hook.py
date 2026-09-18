"""CLIDisplayHook — turns the run's event stream into terminal lines.

A pure event consumer: it implements no interception hooks at all, and every
shape it draws comes from :mod:`mocode.cli.lines`. What it owns is the state of
the run *as it is being drawn* — which tool calls are open, how much of each
one's output has been shown, what the turn has cost so far.

A tool call prints a header only once it has something to say. That keeps a
silent tool to a single line (its verdict), and makes a talkative one read
top-down: header, its output, verdict — instead of the output floating above
the line that names it.
"""

from __future__ import annotations

from ..core.events import (
    Event,
    Notice,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
)
from ..core.hook import AgentHook
from ..core.tool import ToolRegistry
from . import lines as L

#: Lines of a single tool call's output to show live. A command that dumps a
#: thousand lines would otherwise bury the conversation; the model still gets
#: all of it, and the omission is reported rather than hidden.
OUTPUT_LINES = 20


class CLIDisplayHook(AgentHook):
    """Draws the run: streamed text, tool blocks, the rule that closes a turn."""

    def __init__(self, display, tools: ToolRegistry):
        self._d = display
        self._tools = tools
        #: call_id -> (name, args) for calls still in flight
        self._running: dict[str, tuple[str, dict]] = {}
        #: calls whose header has been printed
        self._opened: set[str] = set()
        #: call_id -> displayable lines seen, for the output budget
        self._lines: dict[str, int] = {}

    async def on_event(self, event: Event) -> None:
        match event:
            case TextDelta():
                self._d.stream(event.text, kind="answer")

            case ReasoningDelta():
                self._d.stream(event.text, kind="reasoning")

            case ToolCallStarted():
                self._running[event.call_id] = (event.name, event.args)
                self._lines[event.call_id] = 0
                self._d.end_stream()

            case ToolOutput():
                self._tool_output(event)

            case ToolCallFinished():
                self._tool_done(event)

            case RunFinished():
                self._d.end_stream()
                self._d.render(L.divider())

            case RunFailed():
                self._d.end_stream()
                self._d.error(f"{event.kind}: {event.error}")
                self._d.render(L.divider())

            case Notice():
                {"warn": self._d.warn, "error": self._d.error}.get(
                    event.level, self._d.info
                )(event.message)

            case Event():
                pass  # a core event with nothing to draw

            case _:
                self._d.render_event(event)  # a plugin-defined event

    # ── Tool blocks ────────────────────────────────────────

    def _tool_output(self, event: ToolOutput) -> None:
        """Show a running tool's own output, up to a per-call line budget.

        The budget counts *lines*, not events, so a tool that emits one large
        chunk cannot walk past it.
        """
        state = self._running.get(event.call_id)
        if state is None:
            return  # output for a call we do not know about
        name, args = state

        if event.call_id not in self._opened:
            self._d.render(L.tool_open(name, args, self._tools))
            self._opened.add(event.call_id)

        # Label only while a peer is actually running — that is when two blocks
        # can interleave and a bare line would be unattributable. A batch whose
        # other calls are silent or already done needs no label at all.
        label = f"{name}  " if len(self._running) > 1 else ""

        seen = self._lines.get(event.call_id, 0)
        for line in event.text.splitlines():
            if not line.strip():
                continue
            if seen < OUTPUT_LINES:
                self._d.render(L.tool_output(line, stream=event.stream, label=label))
            seen += 1
        self._lines[event.call_id] = seen

    def _tool_done(self, event: ToolCallFinished) -> None:
        _, args = self._running.pop(event.call_id, (event.name, {}))
        opened = event.call_id in self._opened
        self._opened.discard(event.call_id)

        elided = self._lines.pop(event.call_id, 0) - OUTPUT_LINES
        if elided > 0:
            self._d.render(L.tool_output(f"… +{elided} more lines"))

        self._d.end_stream()
        self._d.render(
            L.tool_close(
                # A peer still in flight means its header may be between this
                # verdict and our own.
                event, args, self._tools, opened=opened, labelled=bool(self._running)
            )
        )

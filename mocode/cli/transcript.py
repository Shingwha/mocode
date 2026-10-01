"""Transcript — the conversation's event stream, folded into a document.

Pure data, no terminal: :meth:`Transcript.apply` folds the same events a
frontend draws into :class:`Block` s built from the
:mod:`~mocode.cli.lines` vocabulary, the way ``RunState`` folds the same
stream into a live snapshot. The same event stream always folds into the same
transcript, so the model a screen is drawn from is testable without one, and a
painter — this one or a web view — is just a projection of it.

The fold mirrors what the live renderer drew, branch for branch: deltas extend
one streaming block per kind, a tool call is a block opened by its start and
landed by its finish, a notice or an unknown event is a block of its own, and
the turn's cost and rule close it. ``apply_history`` is the same fold for a
stored conversation — the generalisation of the history replay — so a resumed
session reads as the blocks a fresh one would have produced.

Blocks answer by identity, not position: a tool block by its ``call_id``, a
plugin message's block by the ``block_id`` the plugin chose. That is what lets
a slow writer update one place on screen without anyone parsing its text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core.events import (
    IterationFinished,
    IterationStarted,
    Notice,
    PluginMessage,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
)
from ..host.events import ConversationChanged
from . import lines as L

if TYPE_CHECKING:
    from ..core.events import Event, Usage
    from ..core.tool import ToolRegistry

#: The block kinds the transcript vocabulary has. A kind says what a block
#: *is*, not where a painter puts it.
KINDS = ("user", "answer", "reasoning", "tool", "notice", "rule", "plugin")

#: How a block stands: still being written, still at work, or final. The
#: transition out of the first two is whatever event completes the block; the
#: last one is permanent — a painter may print a final block into scrollback
#: and forget it.
STREAMING = "streaming"
RUNNING = "running"
DONE = "done"


@dataclass
class Block:
    """One addressable piece of the conversation, as data.

    ``id`` is how later events find the block: a tool call's ``call_id``, a
    plugin message's ``block_id``, or a positional id for kinds the stream
    opens without one. ``meta`` carries what the lines were built from — the
    call's arguments, the turn's usage, a plugin message's payload — so a
    consumer that wants more than the lines never scrapes them.
    """

    id: str
    kind: str
    lines: list[L.Line] = field(default_factory=list)
    state: str = DONE
    meta: dict = field(default_factory=dict)


class Transcript:
    """The document a turn's events fold into."""

    def __init__(self, tools: "ToolRegistry | None" = None, *, drawers=None):
        #: The registry the lines vocabulary reads (summary/result keys).
        #: Call-scoped — ``apply_history`` may replace it.
        self.tools = tools
        #: How message-like events become lines: anything with a
        #: ``lines_for(event) -> list[Line] | None``. ``None`` (no registry
        #: wired) means the built-in shapes below — a transcript stays
        #: constructible on its own, with no frontend around.
        self._drawers = drawers
        self.blocks: list[Block] = []

    # ── The fold ──────────────────────────────────────────

    def apply(self, event: "Event") -> None:
        """Fold one event in. Same stream in, same transcript out."""
        match event:
            case TextDelta():
                self._stream(event.text, "answer")

            case ReasoningDelta():
                self._stream(event.text, "reasoning")

            case ToolCallStarted():
                self._seal_stream()
                self.blocks.append(
                    Block(
                        id=event.call_id or f"tool/{len(self.blocks)}",
                        kind="tool",
                        state=RUNNING,
                        lines=[L.tool_pending(event.name, event.args, self.tools)],
                        meta={"name": event.name, "args": event.args},
                    )
                )

            case ToolOutput():
                # A running tool's output feeds the model, not the reader; the
                # transcript keeps it with the call it came from, for whoever
                # asks (an expandable verdict is a later painter's business).
                block = self._find(event.call_id, "tool", RUNNING)
                if block is not None:
                    block.meta["output"] = block.meta.get("output", "") + event.text

            case ToolCallFinished():
                self._land_tool(event)

            case Notice():
                self._seal_stream()
                self.blocks.append(
                    Block(
                        id=f"notice/{len(self.blocks)}",
                        kind="notice",
                        lines=self._lines_for(event),
                        meta={"level": event.level},
                    )
                )

            case PluginMessage():
                self._plugin_message(event)

            case RunFinished():
                self._end_turn(usage=event.usage)

            case RunFailed():
                self._end_turn(error=f"{event.kind}: {event.error}")

            case ConversationChanged():
                # The history was replaced; this event carries no payload —
                # the conversation is the source of truth, and the reader who
                # owns one refills the document with ``apply_history``.
                self.blocks.clear()

            case RunStarted() | IterationStarted() | IterationFinished():
                pass  # a core event with nothing to draw

            case _:
                # Anything the transcript was not written for — including an
                # event type a plugin defined — still becomes a block: one
                # line, from the event's own description of itself.
                self._seal_stream()
                self.blocks.append(
                    Block(
                        id=f"event/{len(self.blocks)}",
                        kind="notice",
                        lines=self._lines_for(event),
                        meta={"event": event.type},
                    )
                )

    # ── Streamed text ─────────────────────────────────────

    def _stream(self, text: str, kind: str) -> None:
        """Extend the streaming block of *kind*, opening one if none is open."""
        block = self.blocks[-1] if self.blocks else None
        if block is None or block.kind != kind or block.state != STREAMING:
            self._seal_stream()
            block = Block(
                id=f"{kind}/{len(self.blocks)}", kind=kind, state=STREAMING
            )
            block.meta["text"] = ""
            self.blocks.append(block)
        block.meta["text"] += text

    def _seal_stream(self) -> None:
        """Close the open streaming block, materialising its lines."""
        if self.blocks and self.blocks[-1].state == STREAMING:
            block = self.blocks[-1]
            text = block.meta.get("text", "")
            block.lines = L.answer(text) if block.kind == "answer" else L.reasoning(text)
            block.state = DONE

    # ── Tool calls ────────────────────────────────────────

    def _land_tool(self, event: ToolCallFinished) -> None:
        """Replace the call's pending line with its verdict."""
        block = self._find(event.call_id, "tool", RUNNING)
        if block is None:
            # A finish without a start (a fold that joined mid-call) still
            # lands — with no arguments to name it by, as ever.
            block = Block(
                id=event.call_id or f"tool/{len(self.blocks)}",
                kind="tool",
                meta={"name": event.name, "args": {}},
            )
            self.blocks.append(block)
        block.lines = [L.tool_close(event, block.meta.get("args", {}), self.tools)]
        block.state = DONE
        block.meta.update(
            status=event.status,
            result=event.result,
            duration=event.duration,
            details=event.details,
        )

    # ── Plugin messages ───────────────────────────────────

    def _plugin_message(self, event: PluginMessage) -> None:
        """One plugin-authored block, addressed by the ``block_id`` it chose."""
        block = None
        if event.block_id:
            block = self._find(event.block_id, "plugin")
        has_content = bool(event.kind or event.data)
        if block is None:
            block = Block(
                id=event.block_id or f"plugin/{len(self.blocks)}",
                kind="plugin",
                state=RUNNING if has_content and not event.sealed else DONE,
            )
            self.blocks.append(block)
        if has_content:
            new_lines = self._lines_for(event)
            if block.state == DONE:
                # An update after the seal is a late arrival: it follows the
                # block rather than rewriting it — there is no path back into
                # a block a painter may already have committed.
                block.lines.extend(new_lines)
                block.meta.setdefault("follow-ups", []).append(event.data or event.kind)
            else:
                block.lines = new_lines
            block.meta["kind"] = event.kind
            if event.data:
                block.meta["data"] = event.data
        if event.sealed:
            block.state = DONE

    # ── Turn boundary ─────────────────────────────────────

    def _end_turn(self, *, usage: "Usage | None" = None, error: str = "") -> None:
        """Close the turn: what it cost (or why it died), then the rule."""
        self._seal_stream()
        block_lines = []
        if error:
            block_lines.append(L.notice(error, "error"))
        elif usage and (usage.prompt_tokens or usage.completion_tokens):
            block_lines.append(L.tokens(usage))
        block_lines.append(L.divider())
        self.blocks.append(
            Block(
                id=f"rule/{len(self.blocks)}",
                kind="rule",
                lines=block_lines,
                meta={"error": error} if error else {"usage": usage},
            )
        )

    # ── History ───────────────────────────────────────────

    def apply_history(self, messages: list[dict], tools: "ToolRegistry | None" = None) -> None:
        """Rebuild the document from a stored conversation — the resume path.

        Walks the same grouping the flat replay does
        (:func:`mocode.cli.lines.grouped_messages`), so the blocks' lines
        flatten to exactly what ``L.conversation`` drew — one replay
        vocabulary, two shapes.
        """
        if tools is not None:
            self.tools = tools
        self.blocks.clear()
        for msg, results in L.grouped_messages(messages):
            role = msg.get("role")
            index = len(self.blocks)
            if role == "user":
                self.blocks.append(
                    Block(
                        id=f"user/{index}",
                        kind="user",
                        lines=L.prompt(L.flatten_content(msg.get("content", ""))),
                        meta={"history": True},
                    )
                )
            elif role == "assistant":
                if msg.get("reasoning_content"):
                    self.blocks.append(
                        Block(
                            id=f"reasoning/{index}",
                            kind="reasoning",
                            lines=L.reasoning(msg["reasoning_content"]),
                            meta={"history": True},
                        )
                    )
                if msg.get("content"):
                    self.blocks.append(
                        Block(
                            id=f"answer/{index}",
                            kind="answer",
                            lines=L.answer(msg["content"]),
                            meta={"history": True},
                        )
                    )
                if msg.get("tool_calls"):
                    for call in msg["tool_calls"]:
                        self.blocks.append(
                            Block(
                                id=call.get("id", "") or f"tool/{index}",
                                kind="tool",
                                lines=[L.replay_call(call, results.get(call.get("id", ""), ""), self.tools)],
                                meta={
                                    "history": True,
                                    "name": call.get("function", {}).get("name", "?"),
                                },
                            )
                        )
                else:
                    # An assistant message with no tool calls is where a turn
                    # ended, so it carries the rule — same as the live path.
                    self.blocks.append(
                        Block(
                            id=f"rule/{index}", kind="rule", lines=[L.divider()],
                            meta={"history": True},
                        )
                    )

    # ── Internals ─────────────────────────────────────────

    def _find(self, id: str, kind: str, state: str | None = None) -> Block | None:
        """The last block matching an identity — the one later events address."""
        for block in reversed(self.blocks):
            if block.id == id and block.kind == kind and (
                state is None or block.state == state
            ):
                return block
        return None

    def _lines_for(self, event: "Event") -> list[L.Line]:
        """An event as lines: a registered drawer's, else the event's own summary."""
        if self._drawers is not None:
            drawn = self._drawers.lines_for(event)
            if drawn is not None:
                return list(drawn)
        if isinstance(event, Notice):  # the built-in shape when no table is wired
            return [L.notice(event.message, event.level)]
        return [L.notice(event.summary(), "info")]


__all__ = [
    "Block",
    "KINDS",
    "Transcript",
]

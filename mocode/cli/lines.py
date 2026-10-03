"""What a turn looks like — the terminal's vocabulary, as data.

Nothing here prints. A :class:`Line` says how a line should look; the
:class:`~mocode.cli.display.Display` decides where it goes. Two things follow:

* the live renderer and history replay share these builders, so a resumed
  conversation cannot drift from a fresh one;
* the vocabulary is testable without a terminal.

The shape it encodes: **everything starts at column 0, and the first character
says what the line is.** No indentation, because a terminal has no hanging
indent — a long line wraps back to column 0 regardless, and it does so most
often on exactly the content that needs it least (reasoning). A leading
character is a per-line marker that survives wrapping.

    ❯ 帮我看看 tests/
                          ← blank
    用户想知道 tests 目录的内容…       ← reasoning: dimmest, no marker
    · bash  ls tests/…                ← a call in flight: dim, one row per call
    ✓ bash  ls tests/ · exit_code=0 · 0.1s   ← the same row, once it is done

    tests 下有 14 个测试文件。         ← the answer: unmarked and brightest
    ↑1,234 ↓567 tokens                ← what the turn cost
    ──────────────────────────────    ← the turn is over
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from ..core.events import (
    TOOL_DENIED,
    TOOL_ERROR,
    TOOL_OK,
    TOOL_TIMEOUT,
    ToolCallFinished,
)
from ..core.tool import DENIED_PREFIX, ERROR_PREFIX, TIMEOUT_PREFIX, ToolRegistry
from ..core.transcript import (
    answered_call_id,
    is_assistant,
    is_tool_result,
    is_user,
    reasoning_of,
    text_of,
    tool_call_args,
    tool_call_id,
    tool_call_name,
    tool_calls_of,
)
from .text import ellipsize_middle, ellipsize_tail, one_row, terminal_width
from .theme import FAIL, OK, PENDING, RULE, USER

if TYPE_CHECKING:
    from ..core.provider import Usage

#: Width budget for the argument a tool call shows.
SUMMARY_WIDTH = 60
#: Cap on a failure phrase, so one bad call cannot fill the screen.
FAILURE_WIDTH = 80
#: Beyond this the turn rule stops growing — a full-width bar reads as a wall.
DIVIDER_MAX = 72
#: Narrowest a turn rule is allowed to get.
DIVIDER_MIN = 20


@dataclass(frozen=True)
class Line:
    """One line of terminal output: how it looks, not where it goes.

    ``style`` and ``icon_style`` name a :class:`~mocode.cli.theme.Theme` field.
    ``note`` is a dim trailing field — elapsed time, a result detail — joined to
    the text with a ``·`` so it reads as an aside rather than more content.
    """

    text: str = ""
    icon: str = ""
    style: str = "answer"
    icon_style: str = ""
    note: str = ""


# ── the conversation ────────────────────────────────────────


def user(text: str) -> Line:
    """What you typed, behind the marker that invites it."""
    return Line(text=text.strip(), icon=USER, style="user")


def prompt(text: str) -> list[Line]:
    """What you typed, and the blank line that separates it from the reply."""
    return [user(text), Line()]


def divider(width: int | None = None) -> Line:
    """The rule that closes a turn.

    Drawn when the turn ends rather than when the next prompt arrives, so it is
    already on screen while you are still typing — the boundary belongs to the
    turn that just finished. *width* defaults to the terminal's; a caller with
    no terminal (a test, an export) supplies its own.
    """
    if width is None:
        width = max(DIVIDER_MIN, min(terminal_width() - 1, DIVIDER_MAX))
    return Line(text=RULE * width, style="dim")


def tokens(usage: "Usage") -> Line:
    """What the turn cost, as one muted line above the rule.

    Quiet on purpose: it is an aside about the machinery, not part of the
    conversation, so it carries no marker and sits below the answer.
    """
    return Line(
        text=f"↑{usage.prompt_tokens:,} ↓{usage.completion_tokens:,} tokens",
        style="muted",
    )


def answer(text: str) -> list[Line]:
    """The model's prose, unmarked — it is the point, and the default state."""
    return [Line(text=line) for line in text.splitlines()]


def reasoning(text: str) -> list[Line]:
    """Thinking: dimmest, unmarked. Distinguishable from an answer by weight."""
    return [Line(text=line, style="reasoning") for line in text.splitlines()]


def notice(text: str, level: str = "info") -> Line:
    """A message from the host or a plugin. ``level`` picks the colour."""
    return Line(text=text, style={"warn": "warning", "error": "error"}.get(level, "info"))


# ── tool calls ──────────────────────────────────────────────


def tool_summary(name: str, args: dict, tools: ToolRegistry | None = None) -> str:
    """The one argument worth showing for *name*, per its declared ``summary_key``.

    Flattened to one row before it is bounded: a multi-line argument —
    codemode's script — is previewed as code, not printed as a wall of it.
    """
    if not args:
        return ""
    key = ""
    tool = tools.get(name) if tools is not None else None
    if tool is not None:
        key = tool.summary_key
    if not key or key not in args:
        key = next(iter(args))
    return ellipsize_middle(one_row(str(args.get(key, ""))), SUMMARY_WIDTH)


def _identity(name: str, args: dict, tools: ToolRegistry | None) -> str:
    """A call as one field — ``name  argument``, skipping whichever is missing.

    A model occasionally emits a call with no name, and the summary fallback
    then picks an arbitrary argument; joining only the non-empty parts keeps
    that from rendering as a stray double space.
    """
    return "  ".join(p for p in (name, tool_summary(name, args, tools)) if p)


def tool_pending(name: str, args: dict, tools: ToolRegistry | None = None) -> Line:
    """A call while it is still running — the row its verdict will replace.

    Printed when the call starts rather than when it first speaks, so a tool that
    is both slow and quiet still shows that something is running, at no cost in
    lines: the row is rewritten in place when the call ends.
    """
    return Line(text=f"{_identity(name, args, tools)}…", icon=PENDING, style="dim")


def tool_close(
    event: ToolCallFinished,
    args: dict,
    tools: ToolRegistry | None,
) -> Line:
    """A tool call's verdict, carrying its identity and why it ended that way.

    Always the whole line, because it replaces a placeholder: whatever the row
    says afterwards has to stand on its own.
    """
    ok = event.status == TOOL_OK
    return Line(
        text=_identity(event.name, args, tools),
        icon=OK if ok else FAIL,
        icon_style="success" if ok else "error",
        style="accent" if ok else "error",
        note=_verdict(event, tools),
    )


def _verdict(event: ToolCallFinished, tools: ToolRegistry | None) -> str:
    """The aside on a tool line: why it failed, what came back, how long it took."""
    parts: list[str] = []
    if event.status == TOOL_OK:
        detail = _declared_detail(event, tools)
        if detail:
            parts.append(detail)
    else:
        parts.append(_failure_text(event))

    # A timeout already says how long it waited.
    if event.duration >= 0.1 and event.status != TOOL_TIMEOUT:
        parts.append(f"{event.duration:.1f}s")
    return " · ".join(parts)


def _declared_detail(event: ToolCallFinished, tools: ToolRegistry | None) -> str:
    """The one result detail the tool asked to have shown, per its ``result_key``.

    A tool declares one when a fact about the *result* deserves the same line as
    its arguments — bash's exit code, how many lines a read returned.
    """
    tool = tools.get(event.name) if tools is not None else None
    key = tool.result_key if tool is not None else ""
    if not key or key not in event.details:
        return ""
    return f"{key}={event.details[key]}"


def _failure_text(event: ToolCallFinished) -> str:
    """One phrase explaining why a tool call did not succeed."""
    if event.status == TOOL_TIMEOUT:
        return f"timed out after {event.duration:.0f}s"
    if event.status == TOOL_DENIED:
        reason = event.result.removeprefix(f"{DENIED_PREFIX} ").strip()
        return f"denied ({reason})" if reason else "denied"
    text = (event.result or event.status).strip()
    if not text:
        return event.status
    return ellipsize_tail(text.splitlines()[0], FAILURE_WIDTH)


# ── replaying a stored conversation ─────────────────────────

#: A stored tool result records its outcome as a prefix, because a message list
#: has nowhere else to put it. See ``core/tool.py``.
_STATUS_BY_PREFIX = {
    ERROR_PREFIX: TOOL_ERROR,
    TIMEOUT_PREFIX: TOOL_TIMEOUT,
    DENIED_PREFIX: TOOL_DENIED,
}


def conversation(messages: list[dict], tools: ToolRegistry | None = None) -> list[Line]:
    """Replay a stored conversation as the lines a live turn would have drawn.

    Same builders as the live renderer, so resuming a session looks like having
    just run it — the only things missing are a duration (history has none) and
    a tool's live output (it was never stored).
    """
    out: list[Line] = []
    for msg, results in _grouped(messages):
        if is_user(msg):
            out.extend(prompt(text_of(msg)))
        elif is_assistant(msg):
            thinking = reasoning_of(msg)
            if thinking:
                out.extend(reasoning(thinking))
            content = text_of(msg)
            if content:
                out.extend(answer(content))
            calls = tool_calls_of(msg)
            if calls:
                out.extend(_replay_calls(calls, results, tools))
            else:
                # An assistant message with no tool calls is where a turn ended,
                # so it carries the rule — same as the live path.
                out.append(divider())
    return out


def _grouped(messages: list[dict]) -> Iterator[tuple[dict, dict[str, str]]]:
    """Yield each drawable message paired with the tool results that answer it.

    Only user and assistant messages are drawable; the pairing exists so an
    assistant's calls and their results are rendered as one unit, the way the
    live renderer sees them — one batch, one row per call.
    """
    i = 0
    while i < len(messages):
        msg = messages[i]
        if is_tool_result(msg):
            i += 1  # an orphaned result (its assistant is gone) draws nothing
            continue
        results: dict[str, str] = {}
        j = i + 1
        if is_assistant(msg) and tool_calls_of(msg):
            while j < len(messages) and is_tool_result(messages[j]):
                results[answered_call_id(messages[j])] = text_of(messages[j])
                j += 1
        yield msg, results
        i = j


def _replay_calls(
    calls: list[dict], results: dict[str, str], tools: ToolRegistry | None
) -> list[Line]:
    """One assistant message's tool calls, against the results that answered them."""
    out: list[Line] = []
    for call in calls:
        result = results.get(tool_call_id(call), "")
        out.append(
            tool_close(
                ToolCallFinished(
                    call_id=tool_call_id(call),
                    name=tool_call_name(call, "?"),
                    status=_STATUS_BY_PREFIX.get(result.split(":")[0] + ":", TOOL_OK),
                    result=result,
                    duration=-1.0,  # history does not carry timings
                ),
                tool_call_args(call),
                tools,
            )
        )
    return out


__all__ = [
    "FAILURE_WIDTH",
    "Line",
    "SUMMARY_WIDTH",
    "answer",
    "conversation",
    "divider",
    "notice",
    "prompt",
    "reasoning",
    "tokens",
    "tool_close",
    "tool_pending",
    "tool_summary",
    "user",
]

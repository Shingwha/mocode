"""CLI theme — the terminal's whole appearance, in one dataclass.

A style is keyed by *what a line means* (`answer`, `reasoning`, `muted`), not by
where it appears, and its value is the escape sequence to print. Re-skinning is
constructing another ``Theme`` and handing it to
:class:`~mocode.cli.display.Display`; there is deliberately no palette layer in
between, because nothing has ever needed one.
"""

from __future__ import annotations

from dataclasses import dataclass

RESET = "\033[0m"

# ── icons ───────────────────────────────────────────────────
#: What opens a line, so a reader can tell kinds apart at a glance. Plain
#: characters only — a symbol that some terminals render as an emoji takes an
#: unpredictable width and shuffles everything after it.
#:
#: Reasoning deliberately has no marker: it is set apart by dimness alone, so
#: the thinking reads as prose rather than as a marked-up log.
USER = "❯"
#: A tool call while it is still running. Its line is replaced in place by the
#: verdict, so this only ever marks work in flight.
PENDING = "·"
OK = "✓"
FAIL = "✗"
RULE = "─"


@dataclass(frozen=True)
class Theme:
    """Escape sequences and icons. Every field is what it looks like, not where."""

    #: The model talking to you. Left at the terminal's default foreground so
    #: the answer is the brightest thing on screen without any colour at all.
    answer: str = ""
    #: Its thinking. The dimmest text here — present, but never competing.
    reasoning: str = "\033[90m"

    #: What you typed, echoed back above the reply. Bold, and nothing else —
    #: no background block, so it reads as one more line of the conversation
    #: rather than as a filled-in field.
    user: str = "\033[1m"
    accent: str = "\033[96m"
    muted: str = "\033[90m"
    dim: str = "\033[2m"
    success: str = "\033[92m"
    error: str = "\033[91m"
    warning: str = "\033[93m"
    info: str = "\033[36m"

    reset: str = RESET


DEFAULT_THEME = Theme()


__all__ = [
    "DEFAULT_THEME",
    "FAIL",
    "OK",
    "PENDING",
    "RESET",
    "RULE",
    "Theme",
    "USER",
]

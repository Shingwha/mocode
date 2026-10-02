"""Interactive dialogs — the questionary wrappers commands need.

questionary is imported on first use, so importing this module (which happens
when the CLI builds its command set) does not drag the dependency into startup.

A modal exists only where there is someone to answer it: :func:`select` returns
``None`` when stdin or stdout is not a terminal, which is what keeps a piped
``mocode -p "/model"`` from trying to draw a picker into a pipe. Callers already
treat ``None`` as "nothing was chosen", so no command needs a special case.
"""

from __future__ import annotations

import sys

__all__ = ["Choice", "select"]

# Created once on first use.
_style = None


def __getattr__(name: str):
    """Resolve ``Choice`` lazily, for the same reason questionary is lazy."""
    if name == "Choice":
        from questionary import Choice

        globals()["Choice"] = Choice
        return Choice
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _questionary():
    import questionary

    return questionary


def _get_style():
    global _style
    if _style is None:
        _style = _questionary().Style(
            [
                ("qmark", "fg:ansicyan bold"),
                ("question", "bold"),
                ("answer", "fg:ansigreen bold"),
                ("pointer", "fg:ansicyan bold"),
                ("highlighted", "fg:ansicyan bold"),
                ("selected", "fg:ansigreen"),
                ("separator", "fg:ansibrightblack"),
                ("instruction", "fg:ansibrightblack"),
                ("text", ""),
                ("disabled", "fg:ansibrightblack italic"),
            ]
        )
    return _style


def usable() -> bool:
    """Whether a modal can be drawn — both ends have to be a terminal."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (ValueError, OSError):  # detached streams
        return False


async def select(
    title: str,
    choices: list,
    *,
    default: str | None = None,
    instruction: str = "↑↓ navigate, Enter confirm",
) -> str | None:
    """Arrow-key single selection. Returns the chosen value or None if cancelled."""
    if not usable():
        return None
    return await _questionary().select(
        title,
        choices=choices,
        default=default,
        instruction=instruction,
        style=_get_style(),
        qmark="❯",
    ).ask_async()

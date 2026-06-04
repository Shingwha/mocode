"""Interactive CLI prompts — select, multiselect, confirm, text input."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import questionary
from questionary import Choice

from .theme import questionary_style

if TYPE_CHECKING:
    pass

__all__ = ["select", "multiselect", "confirm", "text_input", "Choice"]

# Lazy module-level style — created once on first use.
_style = None


def _get_style():
    global _style
    if _style is None:
        _style = questionary_style()
    return _style


async def select(
    title: str,
    choices: list[str | Choice],
    *,
    default: str | None = None,
    instruction: str = "↑↓ navigate, Enter confirm",
) -> str | None:
    """Arrow-key single selection. Returns chosen value or None (user cancelled)."""
    result = await questionary.select(
        title,
        choices=choices,
        default=default,
        instruction=instruction,
        style=_get_style(),
        qmark="❯",
    ).ask_async()
    return result  # None when user presses Esc / Ctrl+C


async def multiselect(
    title: str,
    choices: list[str | Choice],
    *,
    checked: list[str] | None = None,
    instruction: str = "↑↓ navigate, Space toggle, Enter confirm",
) -> list[str] | None:
    """Multi-selection with space toggle. Returns list of chosen values or None."""
    # Pre-check items via Choice objects when caller passes plain strings + checked list.
    if checked is not None:
        resolved: list[str | Choice] = []
        for c in choices:
            val = (
                c
                if isinstance(c, str)
                else (c.value if c.value is not None else str(c.title))
            )
            if val in checked:
                resolved.append(
                    Choice(
                        title=c if isinstance(c, str) else c.title,
                        value=val,
                        checked=True,
                    )
                )
            else:
                resolved.append(c)
        choices = resolved

    result = await questionary.checkbox(
        title,
        choices=choices,
        instruction=instruction,
        style=_get_style(),
        qmark="❯",
    ).ask_async()
    return result  # None when user presses Esc / Ctrl+C


async def confirm(
    title: str,
    *,
    default: bool = True,
) -> bool | None:
    """Yes/No confirmation. Returns bool or None (user cancelled)."""
    result = await questionary.confirm(
        title,
        default=default,
        style=_get_style(),
        qmark="❯",
    ).ask_async()
    return result  # None when user presses Esc / Ctrl+C


async def text_input(
    title: str,
    *,
    default: str = "",
    validate: Callable[[str], bool | str] | None = None,
    multiline: bool = False,
) -> str | None:
    """Free text input with optional validation. Returns string or None."""
    result = await questionary.text(
        title,
        default=default,
        validate=validate,
        multiline=multiline,
        style=_get_style(),
        qmark="❯",
    ).ask_async()
    return result  # None when user presses Esc / Ctrl+C

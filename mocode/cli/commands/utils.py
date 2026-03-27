"""Command utility functions"""

from typing import Callable, TypeVar

T = TypeVar("T")


def resolve_selection(
    arg: str,
    items: list[str],
    interactive_func: Callable[[], str | None],
) -> str | None:
    """Resolve selection: interactive when no arg, index/name when arg provided."""
    if not arg:
        return interactive_func()

    if arg.isdigit():
        num = int(arg)
        if 1 <= num <= len(items):
            return items[num - 1]
        return None

    return arg


def parse_selection_arg(
    arg: str,
    items: list[T],
    *,
    interactive_func: Callable[[], T | None] | None = None,
    error_handler: Callable[[str], None] | None = None,
) -> T | None:
    """Parse command argument for selection (supports index, direct value)."""
    if not arg:
        if interactive_func:
            return interactive_func()
        return None

    if arg.isdigit():
        num = int(arg)
        if 1 <= num <= len(items):
            return items[num - 1]
        if error_handler:
            error_handler(f"Invalid choice: {num}")
        return None

    if arg in items:
        return arg

    return arg

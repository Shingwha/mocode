"""Command utility functions - pure logic, no UI"""

from typing import TypeVar

T = TypeVar("T")


def resolve_selection(
    arg: str,
    items: list[str],
) -> str | None:
    """Resolve selection: return None when no arg, index/name when arg provided."""
    if not arg:
        return None

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
    error_handler=None,
) -> T | None:
    """Parse command argument for selection (supports index, direct value)."""
    if not arg:
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

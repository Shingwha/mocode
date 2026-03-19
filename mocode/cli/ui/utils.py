"""命令工具函数"""

from typing import Callable, TypeVar

T = TypeVar("T")


def parse_selection_arg(
    arg: str,
    items: list[T],
    *,
    interactive_func: Callable[[], T | None] | None = None,
    error_handler: Callable[[str], None] | None = None,
) -> T | None:
    """
    Parse command argument for selection (supports index, direct value, or interactive).

    Args:
        arg: The command argument (empty string for interactive mode)
        items: List of items for index-based selection
        interactive_func: Function to call for interactive selection (when arg is empty)
        error_handler: Optional error handler for invalid selections

    Returns:
        Selected item or None if cancelled/invalid
    """
    if not arg:
        # No argument: enter interactive mode
        if interactive_func:
            return interactive_func()
        return None

    if arg.isdigit():
        # Numeric selection by index (1-based)
        num = int(arg)
        if 1 <= num <= len(items):
            return items[num - 1]
        if error_handler:
            error_handler(f"Invalid choice: {num}")
        return None

    # Direct value - check if it's in items
    if arg in items:
        return arg

    # Not found in items, return as-is for direct matching
    return arg

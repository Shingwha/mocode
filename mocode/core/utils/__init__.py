"""Core utilities - shared helper functions"""


def preview_result(result: str, max_length: int = 60) -> str:
    """Generate a preview of tool result.

    Args:
        result: Tool result string
        max_length: Maximum length for first line preview

    Returns:
        Truncated preview with line count indicator
    """
    lines = result.split("\n")
    preview = lines[0][:max_length]
    if len(lines) > 1:
        preview += f" ... +{len(lines) - 1} lines"
    elif len(lines[0]) > max_length:
        preview += "..."
    return preview

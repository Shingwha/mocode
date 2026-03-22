"""Tool utility functions"""


def truncate_result(
    result: str,
    limit: int,
    truncate_message: str = "\n...[truncated, use limit/offset parameters to read more]...",
) -> str:
    """Truncate tool result if exceeds limit.

    Args:
        result: The tool result string
        limit: Maximum characters allowed (0 = no limit)
        truncate_message: Message to append when truncated

    Returns:
        Original result if within limit, otherwise truncated with message
    """
    if limit <= 0 or len(result) <= limit:
        return result

    return result[:limit] + truncate_message

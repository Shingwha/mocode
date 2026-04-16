"""Tool utility functions"""


def truncate_result(
    result: str,
    limit: int,
    truncate_message: str = "\n...[truncated, use limit/offset parameters to read more]...",
) -> str:
    """Truncate tool result if exceeds limit."""
    if limit <= 0 or len(result) <= limit:
        return result

    return result[:limit] + truncate_message

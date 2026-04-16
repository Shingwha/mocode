"""Dream tool — LLM 可主动触发记忆整合"""

from ..tool import Tool, ToolRegistry


def register_dream_tools(registry: ToolRegistry, dream) -> None:
    if dream is None:
        return

    async def _dream(args: dict) -> str:
        result = await dream.dream()
        if result.skipped:
            return "Dream skipped: no new summaries to process"
        return (
            f"Dream complete: {result.summaries_processed} summaries processed, "
            f"{result.edits_made} edits made, {result.tool_calls_made} tool calls"
        )

    registry.register(Tool(
        "dream",
        "Trigger a dream cycle to consolidate conversation summaries into long-term memory files.",
        {},
        _dream,
    ))

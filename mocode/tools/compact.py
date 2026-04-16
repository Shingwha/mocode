"""Compact tool — LLM 可主动触发上下文压缩"""

import asyncio

from ..tool import Tool, ToolRegistry


def register_compact_tools(registry: ToolRegistry, compact) -> None:
    if compact is None:
        return

    def _compact(args: dict) -> str:
        messages = registry.state.get("messages")
        if not messages:
            return "No messages to compact"

        new_messages = asyncio.run(
            compact.compact(messages, compact._provider.model)
        )
        messages[:] = new_messages  # 原地替换，Agent.messages 同步变更
        return f"Context compacted: {len(new_messages)} messages remaining"

    registry.register(Tool(
        "compact",
        "Compress conversation history to free up context window. Returns a summary of older messages.",
        {},
        _compact,
    ))

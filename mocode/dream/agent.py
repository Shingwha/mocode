"""Dream Agent — 使用 SubAgent 统一 agent loop

v0.2 关键改进：
- 使用 SubAgent 替代手写 agent loop
- _DreamToolRegistry 包装器处理路径重写
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..paths import MEMORY_DIR
from ..subagent import SubAgent, SubAgentConfig
from ..tool import ToolRegistry
from .prompts import DREAM_SYSTEM_PROMPT, build_dream_prompt

if TYPE_CHECKING:
    from ..provider import Provider

logger = logging.getLogger(__name__)


@dataclass
class DreamAgentResult:
    """Result of a unified dream agent run."""
    tool_calls_made: int = 0
    edits_made: int = 0
    had_error: bool = False


class _DreamToolRegistry:
    """包装 ToolRegistry，将相对路径重写到 MEMORY_DIR"""

    def __init__(self, inner: ToolRegistry):
        self._inner = inner

    def get(self, name: str):
        return self._inner.get(name)

    def all_schemas(self) -> list[dict]:
        return self._inner.all_schemas()

    def run(self, name: str, args: dict) -> str:
        if "path" in args and not _is_absolute(args["path"]):
            args = dict(args)
            filename = args["path"]
            filename = filename.removeprefix("./")
            args["path"] = str(MEMORY_DIR / filename)
        return self._inner.run(name, args)


class DreamAgent:
    """Unified dream agent: single tool-call loop for analysis and editing."""

    def __init__(self, provider: "Provider", tools: ToolRegistry, max_tool_calls: int = 10):
        self._provider = provider
        self._tools = tools
        self._max_tool_calls = max_tool_calls

    async def run(
        self,
        summaries: list[str],
        soul: str,
        user: str,
        memory: str,
    ) -> DreamAgentResult:
        """Run unified analysis+edit cycle. Returns DreamAgentResult."""
        config = SubAgentConfig(
            system_prompt=DREAM_SYSTEM_PROMPT,
            tool_names=["read", "edit", "append"],
            max_tool_calls=self._max_tool_calls,
            max_tokens=2000,
            bypass_permissions=True,
            tool_result_limit=2000,
        )
        wrapped_tools = _DreamToolRegistry(self._tools)
        sub = SubAgent(provider=self._provider, tools=wrapped_tools, config=config)

        user_prompt = build_dream_prompt(summaries, soul, user, memory)
        result = await sub.run(user_prompt)

        # Count edits: build tool_call_id → tool_name map from assistant messages
        tc_names: dict[str, str] = {}
        for msg in result.messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    tc_names[tc["id"]] = tc["function"]["name"]
        edits_made = sum(
            1 for msg in result.messages
            if msg.get("role") == "tool"
            and tc_names.get(msg.get("tool_call_id", "")) in ("edit", "append")
            and msg["content"] == "ok"
        )

        logger.info(
            f"Dream agent completed: {result.tool_calls_made} tool calls, {edits_made} edits"
        )
        return DreamAgentResult(
            tool_calls_made=result.tool_calls_made,
            edits_made=edits_made,
            had_error=result.had_error,
        )


def _is_absolute(path: str) -> bool:
    return (
        path.startswith("/") or
        (len(path) >= 2 and path[1] == ":") or
        path.startswith("\\")
    )

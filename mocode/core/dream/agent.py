"""Dream Agent - unified analysis+editing in a single tool-call loop"""

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .prompts import DREAM_SYSTEM_PROMPT, build_dream_prompt
from ...paths import MEMORY_DIR
from ...tools.base import ToolRegistry

if TYPE_CHECKING:
    from ...providers.openai import AsyncOpenAIProvider

logger = logging.getLogger(__name__)

_DREAM_TOOLS = ["read", "edit", "append"]


@dataclass
class DreamAgentResult:
    """Result of a unified dream agent run."""

    tool_calls_made: int = 0
    edits_made: int = 0
    had_error: bool = False


class DreamAgent:
    """Unified dream agent: single tool-call loop for analysis and editing."""

    def __init__(self, provider: "AsyncOpenAIProvider", max_tool_calls: int = 10):
        self._provider = provider
        self._max_tool_calls = max_tool_calls

    def _get_tool_schemas(self) -> list[dict]:
        """Get schemas for read and edit tools."""
        schemas = []
        for name in _DREAM_TOOLS:
            tool = ToolRegistry.get(name)
            if tool:
                schemas.append(tool.to_schema())
        return schemas

    def _run_tool(self, name: str, args: dict) -> str:
        """Run a tool directly, bypassing permission system."""
        if "path" in args and not _is_absolute(args["path"]):
            args = dict(args)
            filename = args["path"]
            filename = filename.removeprefix("./")
            args["path"] = str(MEMORY_DIR / filename)

        return ToolRegistry.run(name, args)

    async def run(
        self,
        summaries: list[str],
        soul: str,
        user: str,
        memory: str,
    ) -> DreamAgentResult:
        """Run unified analysis+edit cycle. Returns DreamAgentResult."""
        user_prompt = build_dream_prompt(summaries, soul, user, memory)
        messages = [{"role": "user", "content": user_prompt}]
        tool_schemas = self._get_tool_schemas()

        tool_calls_made = 0
        edits_made = 0
        had_error = False

        for _ in range(self._max_tool_calls):
            try:
                response = await self._provider.call(
                    messages=messages,
                    system=DREAM_SYSTEM_PROMPT,
                    tools=tool_schemas,
                    max_tokens=2000,
                )
            except Exception as e:
                logger.error(f"Dream agent LLM call failed: {e}")
                had_error = True
                break

            choice = response.choices[0]
            assistant_msg = choice.message

            # Append assistant response to conversation
            msg_dict = {"role": "assistant", "content": assistant_msg.content or ""}
            if assistant_msg.tool_calls:
                msg_dict["tool_calls"] = [
                    _tc_to_dict(tc) for tc in assistant_msg.tool_calls
                ]
            messages.append(msg_dict)

            # No tool calls means the LLM is done
            if not assistant_msg.tool_calls:
                break

            # Execute tool calls
            for tc in assistant_msg.tool_calls:
                func = tc.function
                tool_name = func.name
                try:
                    tool_args = json.loads(func.arguments) if func.arguments else {}
                except json.JSONDecodeError:
                    tool_args = {}

                result = self._run_tool(tool_name, tool_args)
                tool_calls_made += 1

                # Track edits
                if tool_name in ("edit", "append") and result == "ok":
                    edits_made += 1

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result[:2000],
                })

            if hasattr(choice, "finish_reason") and choice.finish_reason == "stop":
                break

        logger.info(
            f"Dream agent completed: {tool_calls_made} tool calls, {edits_made} edits"
        )
        return DreamAgentResult(
            tool_calls_made=tool_calls_made,
            edits_made=edits_made,
            had_error=had_error,
        )


def _tc_to_dict(tc) -> dict:
    """Convert a tool call object to dict for message history."""
    return {
        "id": tc.id,
        "type": "function",
        "function": {
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        },
    }


def _is_absolute(path: str) -> bool:
    """Check if path is absolute (Windows or POSIX)."""
    return (
        path.startswith("/") or
        (len(path) >= 2 and path[1] == ":") or
        path.startswith("\\")
    )

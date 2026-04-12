"""Dream Editor - Phase 2: execute edit directives via LLM tool calls"""

import json
import logging
from typing import TYPE_CHECKING

from .analyzer import EditDirective
from .prompts import PHASE2_SYSTEM_PROMPT
from ...paths import MEMORY_DIR
from ...tools.base import ToolRegistry

if TYPE_CHECKING:
    from ...providers.openai import AsyncOpenAIProvider

logger = logging.getLogger(__name__)

# Tool schemas exposed to the editor LLM
_DREAM_TOOLS = ["read", "edit"]


class DreamEditor:
    """Phase 2: Execute edit directives using LLM with read/edit tool calls."""

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
        # Resolve relative paths to memory directory for read/edit
        if "path" in args and not _is_absolute(args["path"]):
            args = dict(args)
            filename = args["path"]
            # Normalize: strip leading ./ or just use the filename
            filename = filename.removeprefix("./")
            args["path"] = str(MEMORY_DIR / filename)

        return ToolRegistry.run(name, args)

    async def edit(self, directives: list[EditDirective]) -> int:
        """Execute edit directives. Returns number of tool calls made."""
        if not directives:
            return 0

        # Build instruction message
        instructions = []
        for i, d in enumerate(directives, 1):
            instructions.append(
                f"{i}. [{d.action}] {d.target}: {d.reasoning}\n"
                f"   Content: {d.content}"
            )

        user_msg = (
            f"请执行以下 {len(directives)} 条编辑指令：\n\n"
            + "\n\n".join(instructions)
        )

        messages = [{"role": "user", "content": user_msg}]
        tool_schemas = self._get_tool_schemas()
        tool_calls_made = 0

        for _ in range(self._max_tool_calls):
            try:
                response = await self._provider.call(
                    messages=messages,
                    system=PHASE2_SYSTEM_PROMPT,
                    tools=tool_schemas,
                    max_tokens=2000,
                )
            except Exception as e:
                logger.error(f"Dream Phase 2 LLM call failed: {e}")
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

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result[:2000],  # Truncate large results
                })

            # Stop condition check (finish_reason)
            if hasattr(choice, "finish_reason") and choice.finish_reason == "stop":
                break

        logger.info(f"Dream Phase 2 completed: {tool_calls_made} tool calls")
        return tool_calls_made


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

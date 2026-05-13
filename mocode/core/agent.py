"""AgentLoop — LLM chat engine.

Tool errors propagate as ToolError — AgentLoop catches and returns as tool result.
Cancellation via asyncio.Task.cancel() — CancelledError (BaseException) propagates naturally.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hook import Hooks, MESSAGE_ADDED, TEXT_COMPLETE, TOOL_START, TOOL_COMPLETE, USAGE_UPDATE, PRE_LOOP
from .provider import Provider, Response, Usage
from .tool import ToolError, ToolRegistry


@dataclass
class AgentConfig:
    max_tokens: int = 8192
    tool_result_limit: int = 25000
    tool_timeout: int = 240
    max_iterations: int = 0  # 0 = unlimited


@dataclass
class LoopResult:
    content: str = ""
    iterations: int = 0
    messages: list[dict] = field(default_factory=list)
    had_error: bool = False


class AgentLoop:
    """LLM chat engine — receives all dependencies via constructor."""

    def __init__(
        self,
        provider: Provider,
        system_prompt: str,
        tools: ToolRegistry,
        hooks: Hooks,
        config: AgentConfig | None = None,
    ):
        self.provider = provider
        self.system_prompt = system_prompt
        self._tools = tools
        self.hooks = hooks
        self.config = config or AgentConfig()
        self._messages: list[dict] = []
        self._last_usage: Usage | None = None
        self._call_seq = 0
        self._iteration_count = 0
        self._tool_call_count = 0

    @property
    def messages(self) -> list[dict]:
        return self._messages

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        self._messages = value

    # ---- Chat ----

    async def chat(self, user_input: str, images: list[str] | None = None) -> str:
        """One conversation turn. Cancel via asyncio.Task.cancel()."""
        if images:
            content = self._build_user_content(user_input, images)
        else:
            content = user_input

        msg = await self._emit(MESSAGE_ADDED, {"role": "user", "content": content})
        self.messages.append(msg)

        self.messages = await self.hooks.emit(PRE_LOOP, self.messages)

        return await self._loop()

    async def run_with_messages(self, messages: list[dict]) -> LoopResult:
        """Run the loop with a pre-existing message list (shallow-copied)."""
        self._messages = list(messages)
        try:
            content = await self._loop()
            return LoopResult(
                content=content,
                iterations=self._tool_call_count,
                messages=self._messages,
            )
        except Exception as e:
            return LoopResult(
                content=str(e),
                iterations=self._tool_call_count,
                messages=self._messages,
                had_error=True,
            )

    async def _loop(self) -> str:
        final_response = ""
        self._iteration_count = 0
        self._tool_call_count = 0
        while True:
            response: Response = await self.provider.call(
                self.messages,
                self.system_prompt,
                self._tools.all_schemas(),
                self.config.max_tokens,
            )

            if response.usage:
                self._last_usage = response.usage
                await self._emit(USAGE_UPDATE, {"prompt_tokens": response.usage.prompt_tokens})

            if response.content:
                final_response = await self._emit(TEXT_COMPLETE, response.content)

            if response.tool_calls:
                tool_results = await self._run_tool_calls_parallel(response.tool_calls)
                self._tool_call_count += len(response.tool_calls)
                all_tc_dicts = [
                    {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                    for t in response.tool_calls
                ]
                self.messages.append(self._assistant_msg(response, all_tc_dicts))
                self.messages.extend(tool_results)
                self.messages = await self._emit("post_tool_results", self.messages)
                self._iteration_count += 1
                if self.config.max_iterations > 0 and self._iteration_count >= self.config.max_iterations:
                    break
            else:
                self.messages.append(self._assistant_msg(response))
                break

        return final_response

    @staticmethod
    def _assistant_msg(response, tool_calls=None) -> dict:
        msg: dict = {"role": "assistant", "content": response.content or ""}
        if response.reasoning_content:
            msg["reasoning_content"] = response.reasoning_content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    @staticmethod
    def _build_user_content(text: str, images: list[str]) -> list[dict] | str:
        IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        parts: list[dict] = []

        for path_str in images:
            p = Path(path_str)
            if not p.exists() or p.suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{p.suffix[1:]};base64,{b64}"},
                })
            except Exception:
                continue

        if not parts:
            return text
        if text:
            parts.append({"type": "text", "text": text})
        return parts

    # ---- Tool execution ----

    def _truncate(self, result: str) -> str:
        limit = self.config.tool_result_limit
        if limit > 0 and len(result) > limit:
            return result[:limit] + "\n... [truncated]"
        return result

    async def _run_tool_async(self, tool_name: str, tool_args: dict) -> str:
        """Returns tool result string."""
        call_id = self._next_call_id()

        tool_data = await self._emit(TOOL_START, {"name": tool_name, "args": tool_args, "call_id": call_id})
        tool_name = tool_data["name"]
        tool_args = tool_data["args"]

        tool = self._tools.get(tool_name)
        if tool is None:
            await self._emit(TOOL_COMPLETE, {"name": tool_name, "error": f"unknown tool '{tool_name}'", "call_id": call_id})
            return f"unknown tool '{tool_name}'"

        try:
            if tool.is_async:
                result = await asyncio.wait_for(
                    tool.run_async(tool_args),
                    timeout=self.config.tool_timeout,
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(tool.run, tool_args),
                    timeout=self.config.tool_timeout,
                )
        except asyncio.TimeoutError:
            result = f"timeout: {self.config.tool_timeout}s"
            await self._emit(TOOL_COMPLETE, {"name": tool_name, "timeout": self.config.tool_timeout, "call_id": call_id})
            return self._truncate(result)
        except ToolError as e:
            result = f"{e.code}: {e.message}"
        except Exception as e:
            result = f"error: {e}"

        result = self._truncate(result)
        tc_data = await self._emit(TOOL_COMPLETE, {"name": tool_name, "result": result, "call_id": call_id})
        return tc_data["result"]

    async def _run_tool_calls_parallel(self, tool_calls: list) -> list[dict]:
        async def _run_one(tc):
            tool_args = json.loads(tc.arguments)
            result = await self._run_tool_async(tc.name, tool_args)
            return {"role": "tool", "tool_call_id": tc.id, "content": result}

        if len(tool_calls) == 1:
            try:
                return [await _run_one(tool_calls[0])]
            except Exception as e:
                return [{"role": "tool", "tool_call_id": tool_calls[0].id, "content": f"error: {e}"}]

        raw_results = await asyncio.gather(
            *[_run_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        tool_results = []
        for i, raw in enumerate(raw_results):
            tc = tool_calls[i]
            if isinstance(raw, BaseException):
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": f"error: {raw}"})
            else:
                tool_results.append(raw)

        return tool_results

    # ---- Helpers ----

    async def _emit(self, name: str, data: Any = None) -> Any:
        return await self.hooks.emit(name, data)

    def _next_call_id(self) -> str:
        self._call_seq += 1
        return f"call_{self._call_seq}"

    @property
    def last_usage(self) -> Usage | None:
        return self._last_usage

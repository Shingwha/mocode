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

from .hook import HookRunner, AgentHookContext
from .provider import Provider, Response, Usage, with_retry
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
    tool_calls_made: int = 0
    messages: list[dict] = field(default_factory=list)
    had_error: bool = False


class AgentLoop:
    """LLM chat engine — receives all dependencies via constructor."""

    INTERRUPT_MSG = "[Response was interrupted by the user before completion.]"
    INTERRUPT_TOOL_MSG = (
        "[Tool execution was interrupted by the user before completion.]"
    )

    def __init__(
        self,
        provider: Provider,
        system_prompt: str,
        tools: ToolRegistry,
        hooks: HookRunner,
        config: AgentConfig | None = None,
    ):
        self.provider = provider
        self.system_prompt = system_prompt
        self._tools = tools
        self.hooks = hooks
        self.config = config or AgentConfig()
        self.messages: list[dict] = []
        self._last_usage: Usage | None = None
        self._total_usage = Usage(0, 0)
        self._call_seq = 0
        self._iteration_count = 0
        self._tool_call_count = 0

    # ---- Chat ----

    async def chat(self, user_input: str, images: list[str] | None = None) -> str:
        """One conversation turn. Cancel via asyncio.Task.cancel()."""
        if images:
            content = self._build_user_content(user_input, images)
        else:
            content = user_input

        self.messages.append({"role": "user", "content": content})

        return await self._loop()

    async def run_with_messages(self, messages: list[dict]) -> LoopResult:
        """Run the loop with a pre-existing message list (shallow-copied)."""
        self.messages = list(messages)
        try:
            content = await self._loop()
            return LoopResult(
                content=content,
                tool_calls_made=self._tool_call_count,
                messages=self.messages,
            )
        except Exception as e:
            return LoopResult(
                content=str(e),
                tool_calls_made=self._tool_call_count,
                messages=self.messages,
                had_error=True,
            )

    async def _loop(self) -> str:
        ctx = AgentHookContext(messages=self.messages)
        final_response = ""
        self._iteration_count = 0
        self._tool_call_count = 0
        self._total_usage = Usage(0, 0)

        while True:
            await self.hooks.before_iteration(ctx)
            self.messages = ctx.messages
            if ctx.compact_old:
                await self.hooks.on_compact(ctx)
                ctx.compact_old = 0
                ctx.compact_new = 0

            try:
                response: Response = await with_retry(
                    self.provider.call,
                    self.messages,
                    self.system_prompt,
                    self._tools.all_schemas(),
                    self.config.max_tokens,
                )
            except asyncio.CancelledError:
                self.messages.append(
                    {"role": "assistant", "content": self.INTERRUPT_MSG}
                )
                raise

            ctx.reset_response()
            if response.usage:
                self._last_usage = response.usage
                ctx.usage = response.usage
                self._total_usage = Usage(
                    self._total_usage.prompt_tokens + response.usage.prompt_tokens,
                    self._total_usage.completion_tokens + response.usage.completion_tokens,
                )
            if response.content is not None:
                final_response = response.content
                ctx.final_content = response.content
            if response.reasoning_content:
                ctx.reasoning_content = response.reasoning_content

            ctx.response = response
            ctx.stop_reason = response.finish_reason
            await self.hooks.on_response(ctx)

            if response.tool_calls:
                all_tc_dicts = [
                    {
                        "id": t.id,
                        "type": "function",
                        "function": {"name": t.name, "arguments": t.arguments},
                    }
                    for t in response.tool_calls
                ]
                try:
                    tool_results = await self._run_tool_calls_parallel(
                        response.tool_calls, ctx
                    )
                except asyncio.CancelledError:
                    tool_results = [
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": self.INTERRUPT_TOOL_MSG,
                        }
                        for tc in response.tool_calls
                    ]
                    self.messages.append(self._assistant_msg(response, all_tc_dicts))
                    self.messages.extend(tool_results)
                    raise
                self._tool_call_count += len(response.tool_calls)
                self.messages.append(self._assistant_msg(response, all_tc_dicts))
                self.messages.extend(tool_results)

                ctx.tool_calls = response.tool_calls
                ctx.tool_results = tool_results
                ctx.messages = self.messages
                await self.hooks.after_tools(ctx)
                self.messages = ctx.messages

                self._iteration_count += 1
                if (
                    self.config.max_iterations > 0
                    and self._iteration_count >= self.config.max_iterations
                ):
                    break
            else:
                self.messages.append(self._assistant_msg(response))
                await self.hooks.after_iteration(ctx)
                self.messages = ctx.messages
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

    _IMG_MEDIA = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }

    @staticmethod
    def _build_user_content(text: str, images: list[str]) -> list[dict] | str:
        parts: list[dict] = []

        for path_str in images:
            p = Path(path_str)
            if not p.exists() or p.suffix.lower() not in AgentLoop._IMG_MEDIA:
                continue
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                media_type = AgentLoop._IMG_MEDIA[p.suffix.lower()]
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{b64}"},
                    }
                )
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

    async def _run_tool_async(
        self, tool_name: str, tool_args: dict, ctx: AgentHookContext
    ) -> str:
        """Returns tool result string."""
        call_id = self._next_call_id()

        ctx.reset_tool()
        ctx.tool_name = tool_name
        ctx.tool_args = tool_args
        ctx.tool_call_id = call_id
        await self.hooks.on_tool_start(ctx)

        tool = self._tools.get(tool_name)
        if tool is None:
            ctx.tool_error = f"unknown tool '{tool_name}'"
            await self.hooks.on_tool_complete(ctx)
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
            ctx.tool_timeout = self.config.tool_timeout
            await self.hooks.on_tool_complete(ctx)
            return self._truncate(f"timeout: {self.config.tool_timeout}s")
        except ToolError as e:
            result = f"{e.code}: {e.message}"
            ctx.tool_error = result
        except Exception as e:
            result = f"error: {e}"
            ctx.tool_error = result

        ctx.tool_result = self._truncate(result)
        await self.hooks.on_tool_complete(ctx)
        return ctx.tool_result

    async def _run_tool_calls_parallel(
        self, tool_calls: list, ctx: AgentHookContext
    ) -> list[dict]:
        async def _run_one(tc):
            tool_args = json.loads(tc.arguments)
            # Per-call ctx so concurrent tools don't clobber each other's tool_name/tool_args/tool_error
            tool_ctx = AgentHookContext(messages=ctx.messages)
            result = await self._run_tool_async(tc.name, tool_args, tool_ctx)
            return {"role": "tool", "tool_call_id": tc.id, "content": result}

        raw_results = await asyncio.gather(
            *[_run_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        tool_results = []
        for i, raw in enumerate(raw_results):
            tc = tool_calls[i]
            if isinstance(raw, BaseException):
                tool_results.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": f"error: {raw}"}
                )
            else:
                tool_results.append(raw)

        return tool_results

    # ---- Helpers ----

    def _next_call_id(self) -> str:
        self._call_seq += 1
        return f"call_{self._call_seq}"

    @property
    def tool_registry(self) -> ToolRegistry:
        """Public access to the agent's tool registry."""
        return self._tools

    @property
    def iteration(self) -> int:
        """Number of LLM iterations completed in the last chat() loop.

        Returns tool-call batches + 1 (the final no-tool-call response).
        Minimum 1 after a chat() call.
        """
        return self._iteration_count + 1 if self._iteration_count else 0

    @property
    def last_usage(self) -> Usage | None:
        return self._last_usage

    @property
    def total_usage(self) -> Usage:
        return self._total_usage

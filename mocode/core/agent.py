"""AgentLoop — LLM chat engine.

Tool errors propagate as ToolError — AgentLoop catches and returns as tool result.
Cancellation via asyncio.Task.cancel() — CancelledError (BaseException) propagates naturally.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from .hook import HookRunner, IterationContext, ToolCallContext
from .provider import ModelSpec, Provider, Response, Usage, with_retry
from .tool import (
    DENIED_PREFIX,
    ERROR_PREFIX,
    TIMEOUT_PREFIX,
    ToolError,
    ToolRegistry,
)


@dataclass
class AgentConfig:
    """Loop execution policy. Facts about the model live in :class:`ModelSpec`."""

    tool_result_limit: int = 50000
    tool_timeout: int = 240
    max_iterations: int = 0  # 0 = unlimited

    def replace(self, **changes) -> AgentConfig:
        """Return a copy with the given fields changed (e.g. for derived agents)."""
        return replace(self, **changes)


@dataclass
class LoopResult:
    content: str = ""
    tool_calls_made: int = 0
    messages: list[dict] = field(default_factory=list)
    had_error: bool = False


class AgentLoop:
    """LLM chat engine — receives all dependencies via constructor.

    Public mutable state: ``provider`` (swappable at runtime), ``system_prompt``,
    ``messages``, ``hooks``. Derived agents are created with :meth:`derive`.
    """

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
        model: ModelSpec | None = None,
    ):
        self.provider = provider
        self.model = model if model is not None else ModelSpec(name=provider.model)
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

    def derive(
        self,
        *,
        system_prompt: str | None = None,
        tools: ToolRegistry | None = None,
        hooks: HookRunner | None = None,
        config: AgentConfig | None = None,
        model: ModelSpec | None = None,
    ) -> AgentLoop:
        """Create an independent agent that shares this one's provider.

        Message history is always fresh; everything else is inherited unless
        overridden. This is the primitive behind sub-agents, workflow nodes and
        any other "run a nested agent with narrower tools" feature.
        """
        return AgentLoop(
            provider=self.provider,
            system_prompt=self.system_prompt if system_prompt is None else system_prompt,
            tools=self._tools if tools is None else tools,
            hooks=hooks if hooks is not None else HookRunner(),
            config=self.config if config is None else config,
            model=self.model if model is None else model,
        )

    def reset(self) -> None:
        """Reset mutable state for reuse. Shared deps (provider, prompt, tools) stay."""
        self.messages = []
        self._last_usage = None
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
        ctx = IterationContext(messages=self.messages, emit=self.hooks.on_event)
        final_response = ""
        self._iteration_count = 0
        self._tool_call_count = 0
        self._total_usage = Usage(0, 0)

        while True:
            self._iteration_count += 1
            ctx.iteration = self._iteration_count

            await self.hooks.before_iteration(ctx)
            self.messages = ctx.messages

            try:
                response: Response = await with_retry(
                    self.provider,
                    self.provider.call,
                    self.messages,
                    self.system_prompt,
                    self._tools.all_schemas(),
                    self.model.max_output,
                )
            except asyncio.CancelledError:
                self.messages.append(
                    {"role": "assistant", "content": self.INTERRUPT_MSG}
                )
                raise

            # Reset iteration-level fields
            ctx.final_content = ""
            ctx.reasoning_content = None
            ctx.usage = None
            ctx.stop_reason = None
            ctx.tool_calls = []
            ctx.tool_results = []

            if response.usage:
                self._last_usage = response.usage
                ctx.usage = response.usage
                self._total_usage = Usage(
                    self._total_usage.prompt_tokens + response.usage.prompt_tokens,
                    self._total_usage.completion_tokens
                    + response.usage.completion_tokens,
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
                        response.tool_calls, ctx.emit
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

    async def _run_tool(
        self,
        tool_name: str,
        tool_args: dict,
        emit=None,
    ) -> str:
        """Run one tool call. Returns the tool result string.

        Hooks intercept at two points: ``on_tool_start`` (rewrite args, veto via
        deny) and ``on_tool_complete`` (rewrite the result). ``status`` records
        the structured outcome so callers never parse the result text.
        """
        tc = ToolCallContext(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=self._next_call_id(),
            emit=emit or self.hooks.on_event,
        )
        await self.hooks.on_tool_start(tc)

        if tc.deny:
            tc.status = "denied"
            tc.tool_result = f"{DENIED_PREFIX} {tc.deny}"
        else:
            await self._execute_tool(tc)

        tc.tool_result = self._truncate(tc.tool_result or "")
        await self.hooks.on_tool_complete(tc)
        return tc.tool_result

    async def _execute_tool(self, tc: ToolCallContext) -> None:
        """Execute tc's tool, recording status/result on the context."""
        tool = self._tools.get(tc.tool_name)
        if tool is None:
            tc.status = "not_found"
            tc.tool_result = f"unknown tool '{tc.tool_name}'"
            return

        try:
            if tool.is_async:
                result = await asyncio.wait_for(
                    tool.run_async(tc.tool_args),
                    timeout=self.config.tool_timeout,
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(tool.run, tc.tool_args),
                    timeout=self.config.tool_timeout,
                )
        except asyncio.TimeoutError:
            tc.status = "timeout"
            tc.tool_timeout = self.config.tool_timeout
            tc.tool_result = f"{TIMEOUT_PREFIX} {self.config.tool_timeout}s"
        except ToolError as e:
            tc.status = "error"
            tc.error_code = e.code
            tc.tool_result = f"{e.code}: {e.message}"
        except Exception as e:
            tc.status = "error"
            tc.tool_result = f"{ERROR_PREFIX} {e}"
        else:
            tc.tool_result = result if isinstance(result, str) else str(result)

    async def _run_tool_calls_parallel(self, tool_calls: list, emit) -> list[dict]:
        async def _run_one(tc):
            try:
                tool_args = json.loads(tc.arguments)
            except json.JSONDecodeError as e:
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"error: invalid JSON arguments ({e})",
                }
            result = await self._run_tool(tc.name, tool_args, emit)
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
        """Number of LLM calls made in the last chat() loop."""
        return self._iteration_count

    @property
    def tool_call_count(self) -> int:
        """Number of tool calls executed in the last chat() loop."""
        return self._tool_call_count

    @property
    def last_usage(self) -> Usage | None:
        return self._last_usage

    @property
    def total_usage(self) -> Usage:
        return self._total_usage

"""Agent core - async LLM conversation loop with sequential tool execution"""

import asyncio
from typing import TYPE_CHECKING, Callable

from ..providers.openai import AsyncOpenAIProvider
from ..tools.base import ToolRegistry
from .events import EventType, EventBus
from .permission import CheckOutcome, PermissionChecker
from .interrupt import InterruptToken
from ..plugins import HookRegistry, HookPoint

if TYPE_CHECKING:
    from .config import Config


class AsyncAgent:
    """Async LLM agent with sequential tool execution"""

    def __init__(
        self,
        provider: AsyncOpenAIProvider,
        system_prompt: str,
        max_tokens: int = 8192,
        permission_checker: PermissionChecker | None = None,
        event_bus: EventBus | None = None,
        interrupt_token: InterruptToken | None = None,
        config: "Config | None" = None,
        hook_registry: HookRegistry | None = None,
    ):
        self.provider = provider
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.messages: list = []
        self.permission_checker = permission_checker
        self.event_bus = event_bus or EventBus()
        self.interrupt_token = interrupt_token
        self.config = config
        self.hook_registry = hook_registry
        self.conversation_id: str | None = None

    async def _trigger_hook(self, hook_point: HookPoint, data: dict):
        """Trigger hooks, returns HookContext or None"""
        if not self.hook_registry or not self.hook_registry.has_hooks(hook_point):
            return None
        return await self.hook_registry.trigger(hook_point, data)

    # ---- Event helpers ----

    def _emit_tool_start(self, tool_name: str, tool_args: dict) -> None:
        self.event_bus.emit(EventType.TOOL_START, {
            "name": tool_name, "args": tool_args,
            "conversation_id": self.conversation_id,
        })

    def _emit_tool_complete(self, tool_name: str, result: str) -> None:
        self.event_bus.emit(EventType.TOOL_COMPLETE, {
            "name": tool_name, "result": result,
            "conversation_id": self.conversation_id,
        })

    def _emit_tool_denied(self, tool_name: str, tool_args: dict, reason: str) -> str:
        """Emit tool denial events and return interrupt sentinel"""
        self._emit_tool_start(tool_name, tool_args)
        self._emit_tool_complete(tool_name, f"User {reason} this operation")
        self.event_bus.emit(EventType.INTERRUPTED, {"reason": reason, "tool": tool_name})
        return "[interrupted]"

    # ---- Chat loop ----

    async def chat(self, user_input: str) -> str:
        """Run one conversation turn"""
        if self.interrupt_token:
            self.interrupt_token.reset()

        # Pre-chat hook
        ctx = await self._trigger_hook(HookPoint.AGENT_CHAT_START, {"input": user_input})
        if ctx:
            if ctx.modified and ctx.result:
                user_input = ctx.result
            if ctx.has_error:
                return f"Hook error: {ctx._error}"

        self.messages.append({"role": "user", "content": user_input})
        self.event_bus.emit(EventType.MESSAGE_ADDED, {
            "role": "user", "content": user_input,
            "conversation_id": self.conversation_id,
        })

        final_response = ""

        while True:
            if self.interrupt_token and self.interrupt_token.is_interrupted:
                self.event_bus.emit(EventType.INTERRUPTED, None)
                return "[interrupted]"

            tools = ToolRegistry.all_schemas()
            response = await self._call_with_interrupt_check(
                self.provider.call(
                    self.messages, self.system_prompt, tools, self.max_tokens
                )
            )

            if response is None:
                self.event_bus.emit(EventType.INTERRUPTED, None)
                return "[interrupted]"

            message = response.choices[0].message
            tool_results = []

            if message.content:
                final_response = message.content
                self.event_bus.emit(EventType.TEXT_COMPLETE, {
                    "content": message.content,
                    "conversation_id": self.conversation_id,
                })

            if message.tool_calls:
                for tool_call in message.tool_calls:
                    if self.interrupt_token and self.interrupt_token.is_interrupted:
                        self.event_bus.emit(EventType.INTERRUPTED, None)
                        return "[interrupted]"

                    import json
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    result = await self._run_tool_async(tool_name, tool_args)
                    if result == "[interrupted]":
                        return "[interrupted]"

                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

            self.messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [tc.model_dump() for tc in (message.tool_calls or [])],
            })

            if not tool_results:
                break

            self.messages.extend(tool_results)

        # Post-chat hook
        await self._trigger_hook(
            HookPoint.AGENT_CHAT_END,
            {"response": final_response, "messages": self.messages}
        )

        return final_response

    async def _call_with_interrupt_check(self, coro, check_interval: float = 0.1):
        """Await coroutine while polling for interrupt signal"""
        if not self.interrupt_token:
            return await coro

        task = asyncio.ensure_future(coro) if asyncio.isfuture(coro) else asyncio.create_task(coro)

        try:
            while not task.done():
                if self.interrupt_token.is_interrupted:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    return None

                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=check_interval)
                    return task.result()
                except asyncio.TimeoutError:
                    continue

            return task.result()
        except asyncio.CancelledError:
            return None

    # ---- Tool execution pipeline ----

    def _apply_result_limit(self, result: str) -> str:
        """Truncate result if it exceeds configured limit"""
        if self.config and self.config.tool_result_limit > 0:
            from ..tools.utils import truncate_result
            return truncate_result(result, self.config.tool_result_limit)
        return result

    async def _run_tool_async(self, tool_name: str, tool_args: dict) -> str:
        """Execute a single tool with permission check, hooks, and interrupt support"""

        # 1. Permission check
        if self.permission_checker:
            result = await self.permission_checker.check(tool_name, tool_args)
            if result.outcome == CheckOutcome.DENY:
                return self._emit_tool_denied(tool_name, tool_args, result.reason)
            elif result.outcome == CheckOutcome.USER_INPUT:
                self._emit_tool_start(tool_name, tool_args)
                self._emit_tool_complete(tool_name, result.user_input)
                return result.user_input

        # 2. Pre-run hook
        ctx = await self._trigger_hook(
            HookPoint.TOOL_BEFORE_RUN, {"name": tool_name, "args": tool_args}
        )
        if ctx:
            if ctx.modified and "args" in ctx.data:
                tool_args = ctx.data["args"]
            if ctx.has_error:
                return f"Hook error: {ctx._error}"
            if ctx.result is not None:
                result = self._apply_result_limit(ctx.result)
                self._emit_tool_start(tool_name, tool_args)
                self._emit_tool_complete(tool_name, result)
                return result

        # 3. Execute tool
        self._emit_tool_start(tool_name, tool_args)

        if self.config:
            from ..tools.context import set_tool_context, ToolContext
            set_tool_context(ToolContext(config=self.config))

        result = await self._call_with_interrupt_check(
            asyncio.to_thread(ToolRegistry.run, tool_name, tool_args)
        )

        if result is None:
            return self._emit_tool_denied(tool_name, tool_args, "interrupted")

        # 4. Post-run hook
        ctx = await self._trigger_hook(
            HookPoint.TOOL_AFTER_RUN,
            {"name": tool_name, "result": result, "args": tool_args}
        )
        if ctx and ctx.modified and ctx.result:
            result = ctx.result

        # 5. Truncate and emit
        result = self._apply_result_limit(result)
        self._emit_tool_complete(tool_name, result)
        return result

    def clear(self):
        """Clear conversation history"""
        self.messages.clear()

    def update_provider(self, provider: AsyncOpenAIProvider):
        """Update provider (e.g. when switching models)"""
        self.provider = provider
        self.messages.clear()

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        """Update system prompt"""
        self.system_prompt = prompt
        if clear_history:
            self.messages.clear()

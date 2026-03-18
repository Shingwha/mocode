"""Agent 核心 - 异步版本"""

import asyncio
from typing import TYPE_CHECKING, Callable

from ..providers.openai import AsyncOpenAIProvider
from ..tools.base import ToolRegistry
from .events import EventType, EventBus, get_event_bus
from .permission import PermissionAction, PermissionMatcher, PermissionHandler
from .interrupt import InterruptToken

if TYPE_CHECKING:
    from .config import Config, PermissionConfig


class AsyncAgent:
    """Agent - 异步 LLM 对话循环，顺序执行工具"""

    def __init__(
        self,
        provider: AsyncOpenAIProvider,
        system_prompt: str,
        max_tokens: int = 8192,
        permission_matcher: PermissionMatcher | None = None,
        permission_handler: PermissionHandler | None = None,
        event_bus: EventBus | None = None,
        interrupt_token: InterruptToken | None = None,
        config: "Config | None" = None,
    ):
        self.provider = provider
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.messages: list = []
        self.permission_matcher = permission_matcher
        self.permission_handler = permission_handler
        self.event_bus = event_bus or get_event_bus()
        self.interrupt_token = interrupt_token
        self.config = config

    async def chat(self, user_input: str) -> str:
        """运行一轮异步对话（非流式，工具顺序执行）"""
        # 重置中断状态
        if self.interrupt_token:
            self.interrupt_token.reset()

        self.messages.append({"role": "user", "content": user_input})
        self.event_bus.emit(EventType.MESSAGE_ADDED, {"role": "user", "content": user_input})

        final_response = ""

        while True:
            # 检查中断
            if self.interrupt_token and self.interrupt_token.is_interrupted:
                self.event_bus.emit(EventType.INTERRUPTED, None)
                return "[interrupted]"

            # 异步调用 API（可中断）
            tools = ToolRegistry.all_schemas()
            response = await self._call_with_interrupt_check(
                self.provider.call(
                    self.messages, self.system_prompt, tools, self.max_tokens
                )
            )

            # 如果被中断
            if response is None:
                self.event_bus.emit(EventType.INTERRUPTED, None)
                return "[interrupted]"

            message = response.choices[0].message
            tool_results = []

            # 处理文本内容
            if message.content:
                final_response = message.content
                self.event_bus.emit(EventType.TEXT_COMPLETE, message.content)

            # 处理工具调用（顺序执行）
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    # 工具执行前检查中断
                    if self.interrupt_token and self.interrupt_token.is_interrupted:
                        self.event_bus.emit(EventType.INTERRUPTED, None)
                        return "[interrupted]"

                    import json

                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    # 顺序执行工具（可中断）
                    result = await self._run_tool_async(tool_name, tool_args)

                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )

            # 添加 assistant 消息
            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        tc.model_dump() for tc in (message.tool_calls or [])
                    ],
                }
            )

            # 如果没有工具调用，结束循环
            if not tool_results:
                break

            # 添加工具结果
            self.messages.extend(tool_results)

        return final_response

    async def _call_with_interrupt_check(self, coro, check_interval: float = 0.1):
        """等待协程或 Future，同时检查中断信号

        Args:
            coro: 协程或 Future 对象
            check_interval: 检查间隔（秒）

        Returns:
            协程返回值，如果被中断则返回 None
        """
        if not self.interrupt_token:
            return await coro

        # 创建任务（协程）或直接使用（Future）
        if asyncio.isfuture(coro):
            task = asyncio.ensure_future(coro)
        else:
            task = asyncio.create_task(coro)

        try:
            while not task.done():
                # 检查中断
                if self.interrupt_token.is_interrupted:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    return None

                # 等待一小段时间
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=check_interval)
                    return task.result()
                except asyncio.TimeoutError:
                    # 超时继续检查
                    continue

            return task.result()
        except asyncio.CancelledError:
            return None

    async def _run_tool_async(self, tool_name: str, tool_args: dict) -> str:
        """异步执行单个工具（带权限检查和中断支持）"""
        # 权限检查
        if self.permission_matcher:
            action = self.permission_matcher.check(tool_name, tool_args)

            if action == PermissionAction.DENY:
                return f"User denied tool '{tool_name}'"

            if action == PermissionAction.ASK:
                # 必须有 permission_handler
                if not self.permission_handler:
                    return f"Permission handler required for tool '{tool_name}'"

                user_response = await self.permission_handler.ask_permission(tool_name, tool_args)

                # 处理用户响应
                if user_response == "deny":
                    return f"User denied tool '{tool_name}'"
                elif user_response == "allow":
                    pass  # 继续执行
                else:
                    # 用户输入了自定义内容，作为工具结果返回
                    return user_response

        # 执行工具
        self.event_bus.emit(
            EventType.TOOL_START,
            {
                "name": tool_name,
                "args": tool_args,
            },
        )

        # 设置工具执行上下文（传递 config 给工具）
        if self.config:
            from ..tools.context import set_tool_context
            set_tool_context(self.config)

        # 在线程池中执行同步工具（可中断）
        result = await self._call_with_interrupt_check(
            asyncio.to_thread(ToolRegistry.run, tool_name, tool_args)
        )

        if result is None:
            return "[interrupted]"

        self.event_bus.emit(
            EventType.TOOL_COMPLETE,
            {
                "name": tool_name,
                "result": result,
            },
        )

        return result

    def clear(self):
        """清空对话历史"""
        self.messages.clear()

    def update_provider(self, provider: AsyncOpenAIProvider):
        """更新 provider（切换模型时）"""
        self.provider = provider
        self.messages.clear()

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        """动态更新系统提示

        Args:
            prompt: 新的系统提示
            clear_history: 是否清除历史消息 (避免 prompt 切换后上下文混乱)
        """
        self.system_prompt = prompt
        if clear_history:
            self.messages.clear()

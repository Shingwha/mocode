"""Agent 核心 - 异步版本"""

import asyncio

from ..providers.openai import AsyncOpenAIProvider
from ..tools.base import ToolRegistry
from .events import EventType, events


class AsyncAgent:
    """Agent - 异步 LLM 对话循环，顺序执行工具"""

    def __init__(
        self,
        provider: AsyncOpenAIProvider,
        system_prompt: str,
        max_tokens: int = 8192,
    ):
        self.provider = provider
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.messages: list = []

    async def chat(self, user_input: str) -> str:
        """运行一轮异步对话（非流式，工具顺序执行）"""
        self.messages.append({"role": "user", "content": user_input})
        events.emit(EventType.MESSAGE_ADDED, {"role": "user", "content": user_input})

        final_response = ""

        while True:
            # 异步调用 API（等待完整响应）
            tools = ToolRegistry.all_schemas()
            response = await self.provider.call(
                self.messages, self.system_prompt, tools, self.max_tokens
            )

            message = response.choices[0].message
            tool_results = []

            # 处理文本内容
            if message.content:
                final_response = message.content
                events.emit(EventType.TEXT_COMPLETE, message.content)

            # 处理工具调用（顺序执行）
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    import json

                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    # 顺序执行工具
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

    async def _run_tool_async(self, tool_name: str, tool_args: dict) -> str:
        """异步执行单个工具"""
        events.emit(
            EventType.TOOL_START,
            {
                "name": tool_name,
                "args": tool_args,
            },
        )

        # 在线程池中执行同步工具（避免阻塞事件循环）
        result = await asyncio.to_thread(ToolRegistry.run, tool_name, tool_args)

        events.emit(
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

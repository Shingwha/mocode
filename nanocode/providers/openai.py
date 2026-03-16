"""OpenAI Provider - 使用 OpenAI SDK (异步版)"""

from openai import AsyncOpenAI


class AsyncOpenAIProvider:
    """OpenAI API Provider - 异步非流式"""

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def call(self, messages: list, system: str, tools: list, max_tokens: int):
        """异步调用 OpenAI API，返回完整响应"""
        # 构建消息列表
        openai_messages = [{"role": "system", "content": system}, *messages]

        # 转换工具格式
        openai_tools = [{"type": "function", "function": t} for t in tools] if tools else None

        # 异步调用 API（非流式）
        return await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=openai_tools,
            max_tokens=max_tokens,
        )

"""OpenAI Provider - 使用 OpenAI SDK (异步版)"""

from typing import Any
from openai import AsyncOpenAI


class AsyncOpenAIProvider:
    """OpenAI API Provider - 异步非流式"""

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def call(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Any:
        """异步调用 OpenAI API，返回完整响应"""
        # 构建消息列表
        openai_messages = [{"role": "system", "content": system}, *messages]

        # 异步调用 API（非流式）
        # type: ignore[arg-type] - OpenAI SDK 的类型定义过于严格
        return await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=tools or None,  # type: ignore[arg-type]
            max_tokens=max_tokens,
        )

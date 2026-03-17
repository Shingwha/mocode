"""NanoCode SDK - 嵌入式 AI 编程助手

提供便捷的 SDK 入口，用于将 NanoCode 嵌入到其他应用中。

使用示例:
    import asyncio
    from nanocode import NanoCodeClient, EventType

    async def main():
        # 使用内存配置
        client = NanoCodeClient(config={
            "current": {"provider": "openai", "model": "gpt-4o"},
            "providers": {
                "openai": {
                    "name": "OpenAI",
                    "api_key": "sk-...",
                    "base_url": "https://api.openai.com/v1",
                    "models": ["gpt-4o", "gpt-4o-mini"]
                }
            }
        })

        # 订阅事件
        client.on_event(EventType.TEXT_COMPLETE, lambda e: print(f"[响应] {e.data}"))

        # 发送消息
        response = await client.chat("Hello!")
        print(f"Response: {response}")

    asyncio.run(main())
"""

from typing import Callable

from .core import AsyncAgent, Config, EventBus, EventType, get_event_bus
from .core.permission_handler import PermissionHandler, DefaultPermissionHandler
from .providers import AsyncOpenAIProvider
from .tools import register_all_tools


class NanoCodeClient:
    """便捷的 SDK 入口

    用于将 NanoCode 嵌入到其他应用中，支持：
    - 内存配置（无需文件系统）
    - 独立的事件总线（多租户支持）
    - 灵活的权限处理
    """

    def __init__(
        self,
        config: dict | None = None,
        config_path: str | None = None,
        event_bus: EventBus | None = None,
        permission_handler: PermissionHandler | None = None,
    ):
        """初始化 NanoCode 客户端

        Args:
            config: 配置字典（内存模式），优先级高于 config_path
            config_path: 配置文件路径
            event_bus: 事件总线实例，为 None 时使用默认实例
            permission_handler: 权限处理器，为 None 时使用默认处理器（自动允许）
        """
        # 注册工具（确保工具已注册）
        register_all_tools()

        # 初始化事件总线
        self.event_bus = event_bus or get_event_bus()

        # 加载配置
        self.config = Config.load(path=config_path, data=config)

        # 初始化 Provider
        self.provider = AsyncOpenAIProvider(
            base_url=self.config.base_url or None,
            api_key=self.config.api_key,
            model=self.config.model,
        )

        # 初始化 Agent
        self.agent = AsyncAgent(
            provider=self.provider,
            system_prompt="You are a helpful coding assistant.",
            max_tokens=self.config.max_tokens,
            event_bus=self.event_bus,
        )

        # 权限处理器
        self._permission_handler = permission_handler or DefaultPermissionHandler()

    async def chat(self, message: str) -> str:
        """发送消息并获取响应

        Args:
            message: 用户消息

        Returns:
            助手响应文本
        """
        return await self.agent.chat(message)

    def on_event(self, event_type: EventType, handler: Callable):
        """订阅事件

        Args:
            event_type: 事件类型
            handler: 事件处理器函数
        """
        self.event_bus.on(event_type, handler)

    def off_event(self, event_type: EventType, handler: Callable):
        """取消订阅事件

        Args:
            event_type: 事件类型
            handler: 事件处理器函数
        """
        self.event_bus.off(event_type, handler)

    def clear_history(self):
        """清空对话历史"""
        self.agent.clear()

    def set_model(self, model: str, provider: str | None = None):
        """切换模型

        Args:
            model: 模型名称
            provider: 供应商名称（可选）
        """
        if provider:
            self.config.current.provider = provider
        self.config.model = model

        # 更新 Provider
        self.provider = AsyncOpenAIProvider(
            base_url=self.config.base_url or None,
            api_key=self.config.api_key,
            model=self.config.model,
        )
        self.agent.update_provider(self.provider)

    @property
    def current_model(self) -> str:
        """当前使用的模型"""
        return self.config.model

    @property
    def current_provider(self) -> str:
        """当前使用的供应商"""
        return self.config.current.provider


# 便捷导出
__all__ = [
    "NanoCodeClient",
    "EventBus",
    "EventType",
    "PermissionHandler",
    "DefaultPermissionHandler",
]

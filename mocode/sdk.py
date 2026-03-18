"""mocode SDK - 嵌入式 AI 编程助手

提供便捷的 SDK 入口，用于将 mocode 嵌入到其他应用中。

使用示例:
    import asyncio
    from mocode import MocodeClient, EventType

    async def main():
        # 使用内存配置
        client = MocodeClient(config={
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
from .core.prompts import get_system_prompt
from .core.permission_handler import PermissionHandler, DefaultPermissionHandler
from .core.interrupt import InterruptToken
from .providers import AsyncOpenAIProvider
from .skills import SkillManager
from .tools import register_all_tools


class MocodeClient:
    """便捷的 SDK 入口

    用于将 mocode 嵌入到其他应用中，支持：
    - 内存配置（无需文件系统）
    - 独立的事件总线（多租户支持）
    - 灵活的权限处理
    - 自动加载 Skills
    - 中断支持（通过 interrupt() 方法）
    - 可选的配置持久化
    """

    def __init__(
        self,
        config: dict | None = None,
        config_path: str | None = None,
        event_bus: EventBus | None = None,
        permission_handler: PermissionHandler | None = None,
        interrupt_token: InterruptToken | None = None,
        persistence: bool = True,
    ):
        """初始化 mocode 客户端

        Args:
            config: 配置字典（内存模式），优先级高于 config_path
            config_path: 配置文件路径
            event_bus: 事件总线实例，为 None 时使用默认实例
            permission_handler: 权限处理器，为 NULL 时使用默认处理器（自动允许）
            interrupt_token: 中断信号，为 None 时创建新实例
            persistence: 是否启用配置持久化，为 False 时不保存配置文件
        """
        # 注册工具（确保工具已注册）
        register_all_tools()

        # 初始化事件总线
        self.event_bus = event_bus or get_event_bus()

        # 初始化中断信号
        self._interrupt_token = interrupt_token or InterruptToken()

        # 持久化控制
        self._persistence = persistence

        # 权限处理器（必须在 Agent 初始化前设置）
        self._permission_handler = permission_handler or DefaultPermissionHandler()

        # 加载配置
        self.config = Config.load(path=config_path, data=config)

        # 初始化 Provider
        self.provider = AsyncOpenAIProvider(
            base_url=self.config.base_url or None,
            api_key=self.config.api_key,
            model=self.config.model,
        )

        # 初始化 SkillManager 并获取系统提示
        skill_manager = SkillManager.get_instance()
        system_prompt = get_system_prompt(skill_manager)

        # 初始化 Agent
        self.agent = AsyncAgent(
            provider=self.provider,
            system_prompt=system_prompt,
            max_tokens=self.config.max_tokens,
            event_bus=self.event_bus,
            interrupt_token=self._interrupt_token,
            config=self.config,
            permission_handler=self._permission_handler,
        )

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

    def interrupt(self):
        """中断当前操作"""
        self._interrupt_token.interrupt()

    @property
    def current_model(self) -> str:
        """当前使用的模型"""
        return self.config.model

    @property
    def current_provider(self) -> str:
        """当前使用的供应商"""
        return self.config.current.provider

    @property
    def persistence_enabled(self) -> bool:
        """是否启用了持久化"""
        return self._persistence

    @property
    def providers(self) -> dict:
        """所有供应商配置"""
        return self.config.providers

    @property
    def models(self) -> list[str]:
        """当前供应商的模型列表"""
        return self.config.models

    def get_provider_models(self, provider_key: str) -> list[str]:
        """获取指定供应商的模型列表

        Args:
            provider_key: 供应商 key

        Returns:
            该供应商的模型列表，如果供应商不存在则返回空列表
        """
        prov_config = self.providers.get(provider_key)
        return prov_config.models if prov_config else []

    def save_config(self) -> None:
        """手动保存配置（如果启用了持久化）"""
        if self._persistence:
            self.config.save()

    def set_model(self, model: str, provider: str | None = None):
        """切换模型

        Args:
            model: 模型名称
            provider: 供应商名称（可选）
        """
        if provider:
            self.config.current.provider = provider
        self.config.model = model

        # 如果模型不在当前供应商列表中，添加进去
        pconfig = self.config.current_provider
        if pconfig and model not in pconfig.models:
            pconfig.models.append(model)

        # 更新 Provider
        self.provider = AsyncOpenAIProvider(
            base_url=self.config.base_url or None,
            api_key=self.config.api_key,
            model=self.config.model,
        )
        self.agent.update_provider(self.provider)

        # 自动保存配置
        self.save_config()

    def set_provider(self, provider_key: str, model: str | None = None) -> None:
        """切换供应商

        Args:
            provider_key: 供应商 key
            model: 模型名称（可选，默认使用供应商的第一个模型）
        """
        if provider_key not in self.config.providers:
            raise ValueError(f"Unknown provider: {provider_key}")

        self.config.current.provider = provider_key

        # 获取新供应商的配置
        pconfig = self.config.providers[provider_key]

        # 如果未指定模型，使用供应商的第一个模型
        if model is None:
            model = pconfig.models[0] if pconfig.models else "default"

        # 如果模型不在供应商列表中，添加进去
        if model not in pconfig.models:
            pconfig.models.append(model)

        self.config.current.model = model

        # 更新 Provider
        self.provider = AsyncOpenAIProvider(
            base_url=pconfig.base_url or None,
            api_key=pconfig.api_key,
            model=model,
        )
        self.agent.update_provider(self.provider)

        # 自动保存配置
        self.save_config()


# 便捷导出
__all__ = [
    "MocodeClient",
    "EventBus",
    "EventType",
    "PermissionHandler",
    "DefaultPermissionHandler",
    "InterruptToken",
]

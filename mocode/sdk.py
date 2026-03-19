import os
from typing import TYPE_CHECKING, Callable

from .core import AsyncAgent, Config, EventBus, EventType, get_event_bus
from .core.interrupt import InterruptToken
from .core.permission import (
    DefaultPermissionHandler,
    PermissionHandler,
    PermissionMatcher,
)
from .core.prompt import PromptBuilder
from .core.session import Session, SessionManager
from .plugins import HookRegistry, PluginManager, PluginInfo
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
    - 自定义 Prompt 构建
    - 插件系统支持（Hook 和 Plugin）
    """

    def __init__(
        self,
        config: dict | None = None,
        config_path: str | None = None,
        event_bus: EventBus | None = None,
        permission_handler: PermissionHandler | None = None,
        permission_matcher: PermissionMatcher | None = None,
        interrupt_token: InterruptToken | None = None,
        persistence: bool = True,
        auto_register_tools: bool = True,
        workdir: str | None = None,
        prompt_builder: "PromptBuilder | None" = None,
        hook_registry: HookRegistry | None = None,
        plugin_manager: PluginManager | None = None,
        auto_discover_plugins: bool = True,
    ):
        """初始化 mocode 客户端

        Args:
            config: 配置字典（内存模式），优先级高于 config_path
            config_path: 配置文件路径
            event_bus: 事件总线实例，为 None 时使用默认实例
            permission_handler: 权限处理器，为 NULL 时使用默认处理器（自动允许）
            permission_matcher: 权限匹配器，用于检查工具权限
            interrupt_token: 中断信号，为 None 时创建新实例
            persistence: 是否启用配置持久化，为 False 时不保存配置文件
            auto_register_tools: 是否自动注册工具（幂等操作）
            workdir: 工作目录，用于 session 隔离，默认为当前目录
            prompt_builder: Prompt 构建器，为 None 时使用默认配置
            hook_registry: Hook 注册表，为 None 时创建新实例
            plugin_manager: Plugin 管理器，为 None 时创建新实例
            auto_discover_plugins: 是否自动发现和启用插件
        """
        # 注册工具（幂等操作，可安全多次调用）
        if auto_register_tools:
            register_all_tools()

        # 初始化事件总线
        self.event_bus = event_bus or get_event_bus()

        # 初始化中断信号
        self._interrupt_token = interrupt_token or InterruptToken()

        # 初始化 Hook 注册表
        self.hook_registry = hook_registry or HookRegistry()

        # 初始化 Plugin 管理器
        self.plugin_manager = plugin_manager or PluginManager(
            hook_registry=self.hook_registry
        )

        # 持久化控制
        self._persistence = persistence

        # 工作目录
        self._workdir = workdir or os.getcwd()

        # Session 管理器（懒加载）
        self._session_manager: SessionManager | None = None

        # 当前加载的 session ID（用于更新而非新建）
        self._current_session_id: str | None = None

        # 权限处理器（必须在 Agent 初始化前设置）
        self._permission_handler = permission_handler or DefaultPermissionHandler()

        # 权限匹配器
        self._permission_matcher = permission_matcher

        # 加载配置
        self.config = Config.load(path=config_path, data=config)

        # 初始化 Provider
        self.provider = AsyncOpenAIProvider(
            base_url=self.config.base_url or None,
            api_key=self.config.api_key,
            model=self.config.model,
        )

        # 初始化 SkillManager
        skill_manager = SkillManager.get_instance()

        # 初始化 Prompt 构建器
        if prompt_builder:
            self._prompt_builder = prompt_builder
        else:
            from .core.prompt import default_prompt

            self._prompt_builder = default_prompt()

        # 构建系统提示
        system_prompt = self._prompt_builder.context(
            skill_manager=skill_manager,
            cwd=self._workdir,
        ).build()

        # 初始化 Agent
        self.agent = AsyncAgent(
            provider=self.provider,
            system_prompt=system_prompt,
            max_tokens=self.config.max_tokens,
            event_bus=self.event_bus,
            interrupt_token=self._interrupt_token,
            config=self.config,
            permission_handler=self._permission_handler,
            permission_matcher=self._permission_matcher,
            hook_registry=self.hook_registry,
        )

        # 自动发现和启用插件
        if auto_discover_plugins:
            self._init_plugins()

    def _init_plugins(self) -> None:
        """Initialize plugins from config"""
        # Discover plugins and auto-enable builtins (respecting disabled list)
        self.plugin_manager.discover_and_enable_builtins(
            disabled_list=self.config.plugins.disabled
        )

        # Enable plugins from config (user preferences override defaults)
        enabled_plugins = self.config.plugins.enabled
        for plugin_name in enabled_plugins:
            self.plugin_manager.enable(plugin_name)

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

    @property
    def workdir(self) -> str:
        """当前工作目录"""
        return self._workdir

    @property
    def session_manager(self) -> SessionManager:
        """Session 管理器（懒加载）"""
        if self._session_manager is None:
            self._session_manager = SessionManager(self._workdir)
        return self._session_manager

    def list_sessions(self) -> list[Session]:
        """列出当前工作目录的所有 session

        Returns:
            Session 列表，按更新时间降序排列
        """
        return self.session_manager.list_sessions()

    def save_session(self) -> Session:
        """保存当前对话为 session

        如果已加载 session 则更新，否则新建。

        Returns:
            保存的 Session 对象
        """
        if self._current_session_id:
            # 更新已存在的 session
            session = self.session_manager.update_session(
                session_id=self._current_session_id,
                messages=self.agent.messages,
                model=self.current_model,
                provider=self.current_provider,
            )
            if session:
                return session
            # 如果更新失败（session 被删除），则新建

        # 新建 session
        session = self.session_manager.save_session(
            messages=self.agent.messages,
            model=self.current_model,
            provider=self.current_provider,
        )
        self._current_session_id = session.id
        return session

    def load_session(self, session_id: str) -> Session | None:
        """加载指定 session

        Args:
            session_id: session ID

        Returns:
            Session 对象，如果不存在则返回 None
        """
        session = self.session_manager.load_session(session_id)
        if session:
            # 只恢复 messages，不切换 model/provider
            self.agent.messages = session.messages.copy()
            # 记录当前 session ID
            self._current_session_id = session_id
        return session

    def clear_history_with_save(self) -> Session | None:
        """清空历史并自动保存

        Returns:
            保存的 session（如果有消息的话），否则返回 None
        """
        saved = None
        if self.agent.messages:
            saved = self.save_session()
        self.agent.clear()
        # 清空后重置 session ID，下次对话从新 session 开始
        self._current_session_id = None
        return saved

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

    def add_provider(
        self,
        key: str,
        name: str,
        base_url: str,
        api_key: str = "",
        models: list[str] | None = None,
        set_current: bool = False,
    ) -> None:
        """添加新的供应商配置

        Args:
            key: 供应商唯一标识符（如 'openai', 'anthropic'）
            name: 显示名称
            base_url: API 端点 URL
            api_key: API 密钥，可为空
            models: 支持的模型列表
            set_current: 是否切换到此供应商

        Raises:
            ValueError: 如果 key 已存在
        """
        from .core.config import ProviderConfig

        if key in self.config.providers:
            raise ValueError(f"Provider '{key}' already exists")

        self.config.providers[key] = ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=api_key,
            models=models or [],
        )

        if set_current:
            self.set_provider(key)
        else:
            self.save_config()

    def add_model(
        self, model: str, provider: str | None = None, set_current: bool = False
    ) -> None:
        """添加新模型到供应商

        Args:
            model: 模型名称
            provider: 供应商 key（可选，默认当前供应商）
            set_current: 是否切换到此模型

        Raises:
            ValueError: 如果供应商不存在
        """
        provider_key = provider or self.current_provider
        if provider_key not in self.config.providers:
            raise ValueError(f"Provider '{provider_key}' does not exist")

        pconfig = self.config.providers[provider_key]
        if model not in pconfig.models:
            pconfig.models.append(model)

        if set_current:
            if provider:
                self.set_provider(provider, model)
            else:
                self.set_model(model)
        else:
            self.save_config()

    def remove_provider(self, key: str) -> None:
        """删除供应商

        Args:
            key: 供应商 key

        Raises:
            ValueError: 如果供应商不存在或是最后一个供应商
        """
        if key not in self.config.providers:
            raise ValueError(f"Provider '{key}' does not exist")

        if len(self.config.providers) <= 1:
            raise ValueError("Cannot remove the last provider")

        # 如果删除的是当前使用的 provider，切换到第一个可用的
        if self.current_provider == key:
            other_key = next(k for k in self.config.providers.keys() if k != key)
            self.set_provider(other_key)

        # 删除 provider
        del self.config.providers[key]
        self.save_config()

    def update_provider(
        self,
        key: str,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """更新供应商配置

        Args:
            key: 供应商 key
            name: 新的显示名称（可选）
            base_url: 新的 API 端点（可选）
            api_key: 新的 API 密钥（可选）

        Raises:
            ValueError: 如果供应商不存在
        """
        if key not in self.config.providers:
            raise ValueError(f"Provider '{key}' does not exist")

        pconfig = self.config.providers[key]

        if name is not None:
            pconfig.name = name
        if base_url is not None:
            pconfig.base_url = base_url
        if api_key is not None:
            pconfig.api_key = api_key

        # 如果是当前 provider，更新 provider 实例
        if self.current_provider == key:
            self.provider = AsyncOpenAIProvider(
                base_url=pconfig.base_url or None,
                api_key=pconfig.api_key,
                model=self.current_model,
            )
            self.agent.update_provider(self.provider)

        self.save_config()

    def remove_model(self, model: str, provider: str | None = None) -> None:
        """从供应商删除模型

        Args:
            model: 模型名称
            provider: 供应商 key（可选，默认当前供应商）

        Raises:
            ValueError: 如果供应商不存在或模型不存在
        """
        provider_key = provider or self.current_provider
        if provider_key not in self.config.providers:
            raise ValueError(f"Provider '{provider_key}' does not exist")

        pconfig = self.config.providers[provider_key]
        if model not in pconfig.models:
            raise ValueError(f"Model '{model}' does not exist in provider '{provider_key}'")

        # 如果删除的是当前模型，切换到该 provider 的第一个可用模型
        if self.current_provider == provider_key and self.current_model == model:
            if pconfig.models:
                new_model = pconfig.models[0] if pconfig.models[0] != model else (pconfig.models[1] if len(pconfig.models) > 1 else "default")
                self.set_model(new_model)

        pconfig.models.remove(model)
        self.save_config()

    @property
    def prompt_builder(self) -> "PromptBuilder":
        """Prompt 构建器"""
        return self._prompt_builder

    def rebuild_system_prompt(
        self, context: dict | None = None, clear_history: bool = False
    ) -> None:
        """重新构建系统提示

        Args:
            context: 额外的上下文变量
            clear_history: 是否清除历史消息
        """
        ctx = context or {}
        skill_manager = SkillManager.get_instance()
        self._prompt_builder.clear_caches()
        system_prompt = self._prompt_builder.context(
            skill_manager=skill_manager, cwd=self._workdir, **ctx
        ).build()
        self.agent.update_system_prompt(system_prompt, clear_history=clear_history)

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        """直接更新系统提示

        Args:
            prompt: 新的系统提示
            clear_history: 是否清除历史消息
        """
        self.agent.update_system_prompt(prompt, clear_history=clear_history)

    # Plugin management methods

    def list_plugins(self) -> list[PluginInfo]:
        """List all discovered plugins"""
        return self.plugin_manager.list_plugins()

    def enable_plugin(self, name: str) -> bool:
        """Enable a plugin by name

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        success = self.plugin_manager.enable(name)
        if success:
            # Update config
            if name not in self.config.plugins.enabled:
                self.config.plugins.enabled.append(name)
            if name in self.config.plugins.disabled:
                self.config.plugins.disabled.remove(name)
            self.save_config()
        return success

    def disable_plugin(self, name: str) -> bool:
        """Disable a plugin by name

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        success = self.plugin_manager.disable(name)
        if success:
            # Update config
            if name not in self.config.plugins.disabled:
                self.config.plugins.disabled.append(name)
            if name in self.config.plugins.enabled:
                self.config.plugins.enabled.remove(name)
            self.save_config()
        return success

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get plugin info by name

        Args:
            name: Plugin name

        Returns:
            PluginInfo if found, None otherwise
        """
        return self.plugin_manager.get_plugin_info(name)


# 便捷导出
__all__ = [
    "MocodeClient",
    "EventBus",
    "EventType",
    "PermissionMatcher",
    "PermissionHandler",
    "DefaultPermissionHandler",
    "InterruptToken",
    "Session",
    "SessionManager",
    # Prompt system (re-exported from core)
    "PromptBuilder",
    "StaticSection",
    "DynamicSection",
    "default_prompt",
    "minimal_prompt",
    "custom_prompt",
    # Plugin system
    "HookRegistry",
    "PluginManager",
    "PluginInfo",
]

# Re-export prompt types for convenience
from .core.prompt import (
    DynamicSection,
    PromptBuilder,
    StaticSection,
    custom_prompt,
    default_prompt,
    minimal_prompt,
)

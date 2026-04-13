"""配置管理 - 支持多供应商"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any, Callable
import json
import os

from .permission import PermissionConfig
from ..paths import CONFIG_PATH


@dataclass
class CompactConfig:
    """上下文压缩配置"""

    enabled: bool = True
    threshold: float = 0.80
    keep_recent_turns: int = 1
    context_windows: dict[str, int] = field(default_factory=dict)


@dataclass
class DreamConfig:
    """Dream 系统配置（离线记忆整理）"""

    enabled: bool = True
    interval_seconds: int = 7200  # 2 小时
    max_tool_calls: int = 10  # Phase 2 工具调用上限
    max_snapshots: int = 50  # 最大快照保留数


@dataclass
class ModeConfig:
    """模式配置（内存结构，不持久化）"""
    name: str
    auto_approve: bool = False
    dangerous_patterns: list[str] = field(default_factory=list)


@dataclass
class ProviderConfig:
    """供应商配置"""

    name: str  # 显示名称
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)


@dataclass
class CurrentConfig:
    """当前使用配置"""

    provider: str = "zhipu"  # 供应商 key
    model: str = "glm-5"


@dataclass
class Config:
    """Application configuration with multi-provider support"""

    current: CurrentConfig = field(default_factory=CurrentConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    permission: PermissionConfig = field(default_factory=PermissionConfig)
    max_tokens: int = 8192
    tool_result_limit: int = 25000  # Max characters for tool results (0 = no limit)
    tool_timeout: int = 120  # Max seconds for a single tool execution
    gateway: dict = field(default_factory=dict)  # Gateway configuration
    compact: CompactConfig = field(default_factory=CompactConfig)
    dream: DreamConfig = field(default_factory=DreamConfig)

    CONFIG_PATH: Path = CONFIG_PATH

    # Mode config (in-memory, not persisted)
    modes: dict[str, ModeConfig] = field(init=False)
    current_mode: str = field(default="normal", init=False)

    # Persistence (internal, not serialized)
    _persistence_enabled: bool = field(default=True, init=False, repr=False)
    _on_change: Callable[[], None] | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        """Ensure default providers exist"""
        if not self.providers:
            self._init_default_providers()
        if not hasattr(self, 'modes'):
            self.modes = self._default_modes()
        if not hasattr(self, 'current_mode'):
            self.current_mode = "normal"

    def set_persistence(self, enabled: bool, on_change: Callable[[], None] | None = None) -> None:
        """Configure persistence behavior"""
        self._persistence_enabled = enabled
        if on_change is not None:
            self._on_change = on_change

    def _persist(self) -> None:
        """Save and notify if persistence is enabled"""
        if self._persistence_enabled:
            self.save()
        if self._on_change:
            self._on_change()

    def _init_default_providers(self):
        """初始化默认供应商"""
        self.providers = {
            "zhipu": ProviderConfig(
                name="Zhipu",
                base_url="https://open.bigmodel.cn/api/coding/paas/v4/",
                api_key="",
                models=["glm-5.1", "glm-5"],
            ),
            "step": ProviderConfig(
                name="Step",
                base_url="https://api.stepfun.com/step_plan/v1",
                api_key="",
                models=["step-3.5-flash", "step-3.5-flash-2603"],
            ),
        }

    def _default_modes(self) -> dict[str, ModeConfig]:
        """获取默认 mode 配置"""
        return {
            "normal": ModeConfig(name="normal", auto_approve=False),
            "yolo": ModeConfig(
                name="yolo",
                auto_approve=True,
                dangerous_patterns=[
                    "rm ", "rm\t", "rmdir ", "rd ",
                    "dd ", "mv ", "del ", "copy ", "xcopy ",
                    "chmod ", "chown ", "sudo ", "format ", "mkfs ", "fdisk ",
                ]
            )
        }

    def set_mode(self, mode_name: str) -> bool:
        """切换模式

        Args:
            mode_name: 模式名称

        Returns:
            是否成功切换
        """
        if mode_name in self.modes:
            self.current_mode = mode_name
            return True
        return False

    @classmethod
    def load(cls, path: str | None = None, data: dict | None = None) -> "Config":
        """加载配置

        Args:
            path: 配置文件路径，默认使用 CONFIG_PATH
            data: 直接从字典加载（内存模式），优先级高于文件

        Returns:
            Config 实例
        """
        # 内存模式：直接从字典加载
        if data is not None:
            return cls.from_dict(data)

        # 文件模式：从文件加载
        config = cls()
        config_path = Path(path) if path else cls.CONFIG_PATH

        if config_path.exists():
            try:
                file_data = json.loads(config_path.read_text(encoding="utf-8-sig"))
                config._apply_dict(file_data)
            except (json.JSONDecodeError, IOError, TypeError):
                pass

        # 环境变量覆盖 OpenAI key
        return config

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """从字典创建配置（内存模式）"""
        config = cls()
        config._apply_dict(data)
        return config

    def _apply_dict(self, data: dict):
        """应用字典数据到配置"""
        # 加载当前配置
        if "current" in data:
            self.current = CurrentConfig(**data["current"])

        # 加载供应商配置（替换而非合并）
        if "providers" in data:
            self.providers = {}
            for key, pdata in data["providers"].items():
                self.providers[key] = ProviderConfig(**pdata)

        # 加载其他配置
        if "max_tokens" in data:
            self.max_tokens = data["max_tokens"]

        # 加载权限配置
        if "permission" in data:
            self.permission = PermissionConfig.from_dict(data["permission"])

        # 加载工具结果限制
        if "tool_result_limit" in data:
            self.tool_result_limit = data["tool_result_limit"]

        # 加载工具超时
        if "tool_timeout" in data:
            self.tool_timeout = data["tool_timeout"]

        # 加载 Gateway 配置
        if "gateway" in data:
            self.gateway = data["gateway"]

        # 加载 Compact 配置
        if "compact" in data:
            self.compact = CompactConfig(**data["compact"])

        # 加载 Dream 配置
        if "dream" in data:
            self.dream = DreamConfig(**data["dream"])

    def save(self):
        """保存配置"""
        self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # 序列化
        data = {
            "current": asdict(self.current),
            "providers": {k: asdict(v) for k, v in self.providers.items()},
            "permission": self.permission.to_dict(),
            "max_tokens": self.max_tokens,
            "tool_result_limit": self.tool_result_limit,
            "tool_timeout": self.tool_timeout,
            "gateway": self.gateway,
            "compact": asdict(self.compact),
            "dream": asdict(self.dream),
        }

        self.CONFIG_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # 便捷属性
    @property
    def current_provider(self) -> Optional[ProviderConfig]:
        """获取当前供应商配置"""
        return self.providers.get(self.current.provider)

    @property
    def model(self) -> str:
        """获取当前模型"""
        return self.current.model

    @model.setter
    def model(self, value: str):
        """设置当前模型"""
        self.current.model = value

    @property
    def api_key(self) -> str:
        """获取当前 API key"""
        provider = self.current_provider
        return provider.api_key if provider else ""

    @property
    def base_url(self) -> str:
        """获取当前 base URL"""
        provider = self.current_provider
        return provider.base_url if provider else ""

    @property
    def models(self) -> list[str]:
        """获取当前供应商的模型列表"""
        provider = self.current_provider
        return provider.models if provider else []

    @property
    def display_name(self) -> str:
        """显示名称"""
        provider = self.current_provider
        name = provider.name if provider else self.current.provider
        return f"{self.current.model} ({name})"

    # Mutation methods

    def set_model(self, model: str, provider: str | None = None) -> bool:
        """Set current model, optionally switching provider

        Args:
            model: Model name
            provider: Provider key (optional)

        Returns:
            True if current provider was updated
        """
        if provider:
            self.current.provider = provider
        self.current.model = model

        # Add model to provider's list if not present
        pconfig = self.current_provider
        if pconfig and model not in pconfig.models:
            pconfig.models.append(model)

        result = provider is None or provider == self.current.provider
        self._persist()
        return result

    def set_provider(self, provider_key: str, model: str | None = None) -> bool:
        """Switch to a provider

        Args:
            provider_key: Provider key
            model: Model name (optional, defaults to first model)

        Returns:
            True if successful

        Raises:
            ValueError: If provider doesn't exist
        """
        if provider_key not in self.providers:
            raise ValueError(f"Unknown provider: {provider_key}")

        self.current.provider = provider_key
        pconfig = self.providers[provider_key]

        # Default to first model if not specified
        if model is None:
            model = pconfig.models[0] if pconfig.models else "default"

        # Add model to provider's list if not present
        if model not in pconfig.models:
            pconfig.models.append(model)

        self.current.model = model
        self._persist()
        return True

    def add_provider(
        self,
        key: str,
        name: str,
        base_url: str,
        api_key: str = "",
        models: list[str] | None = None,
    ) -> None:
        """Add a new provider

        Args:
            key: Provider unique key
            name: Display name
            base_url: API endpoint URL
            api_key: API key
            models: List of supported models

        Raises:
            ValueError: If key already exists
        """
        if key in self.providers:
            raise ValueError(f"Provider '{key}' already exists")

        self.providers[key] = ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=api_key,
            models=models or [],
        )
        self._persist()

    def add_model(self, model: str, provider: str | None = None) -> None:
        """Add model to provider's list

        Args:
            model: Model name
            provider: Provider key (optional, defaults to current)

        Raises:
            ValueError: If provider doesn't exist
        """
        provider_key = provider or self.current.provider
        if provider_key not in self.providers:
            raise ValueError(f"Provider '{provider_key}' does not exist")

        pconfig = self.providers[provider_key]
        if model not in pconfig.models:
            pconfig.models.append(model)
        self._persist()

    def remove_provider(self, key: str) -> str | None:
        """Remove a provider

        Args:
            key: Provider key

        Returns:
            New current provider key if current was removed, None otherwise

        Raises:
            ValueError: If provider doesn't exist or is the last one
        """
        if key not in self.providers:
            raise ValueError(f"Provider '{key}' does not exist")

        if len(self.providers) <= 1:
            raise ValueError("Cannot remove the last provider")

        new_current = None
        if self.current.provider == key:
            # Switch to another provider
            other_key = next(k for k in self.providers.keys() if k != key)
            self.current.provider = other_key
            pconfig = self.providers[other_key]
            self.current.model = pconfig.models[0] if pconfig.models else "default"
            new_current = other_key

        del self.providers[key]
        self._persist()
        return new_current

    def remove_model(self, model: str, provider: str | None = None) -> str | None:
        """Remove model from provider's list

        Args:
            model: Model name
            provider: Provider key (optional, defaults to current)

        Returns:
            New model if current was removed, None otherwise

        Raises:
            ValueError: If provider or model doesn't exist
        """
        provider_key = provider or self.current.provider
        if provider_key not in self.providers:
            raise ValueError(f"Provider '{provider_key}' does not exist")

        pconfig = self.providers[provider_key]
        if model not in pconfig.models:
            raise ValueError(
                f"Model '{model}' does not exist in provider '{provider_key}'"
            )

        new_model = None
        if self.current.provider == provider_key and self.current.model == model:
            # Switch to another model
            remaining = [m for m in pconfig.models if m != model]
            new_model = remaining[0] if remaining else "default"
            self.current.model = new_model

        pconfig.models.remove(model)
        self._persist()
        return new_model

    def update_provider(
        self,
        key: str,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> bool:
        """Update provider configuration

        Args:
            key: Provider key
            name: New display name (optional)
            base_url: New API endpoint (optional)
            api_key: New API key (optional)

        Returns:
            True if current provider was updated

        Raises:
            ValueError: If provider doesn't exist
        """
        if key not in self.providers:
            raise ValueError(f"Provider '{key}' does not exist")

        pconfig = self.providers[key]
        if name is not None:
            pconfig.name = name
        if base_url is not None:
            pconfig.base_url = base_url
        if api_key is not None:
            pconfig.api_key = api_key

        result = self.current.provider == key
        self._persist()
        return result

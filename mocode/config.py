"""Config dataclass — 纯数据，无持久化逻辑

v0.2 关键改进：Config 不调用 save()、不持有 CONFIG_PATH、不触发 _on_change。
所有 mutation 方法只改数据，调用者负责持久化。
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .permission import PermissionConfig


@dataclass
class CompactConfig:
    """上下文压缩配置"""

    enabled: bool = True
    threshold: float = 0.80
    keep_recent_turns: int = 0
    context_windows: dict[str, int] = field(default_factory=dict)


@dataclass
class DreamConfig:
    """Dream 系统配置"""

    enabled: bool = True
    interval_seconds: int = 7200
    max_tool_calls: int = 10
    max_snapshots: int = 50


@dataclass
class ModeConfig:
    """模式配置（内存态，不持久化）"""

    name: str
    auto_approve: bool = False
    dangerous_patterns: list[str] = field(default_factory=list)


@dataclass
class ProviderConfig:
    """供应商配置"""

    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)
    extra_body: dict[str, dict[str, Any]] | None = None


@dataclass
class CurrentConfig:
    """当前使用配置"""

    provider: str = "zhipu"
    model: str = "glm-5"


@dataclass
class Config:
    """纯配置数据 — 无持久化逻辑"""

    current: CurrentConfig = field(default_factory=CurrentConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    permission: PermissionConfig = field(default_factory=PermissionConfig)
    max_tokens: int = 8192
    tool_result_limit: int = 25000
    tool_timeout: int = 240
    compact: CompactConfig = field(default_factory=CompactConfig)
    dream: DreamConfig = field(default_factory=DreamConfig)

    # 内存态（不持久化）
    modes: dict[str, ModeConfig] = field(init=False)
    current_mode: str = field(default="normal", init=False)

    def __post_init__(self):
        if not self.providers:
            self._init_default_providers()
        if not hasattr(self, "modes"):
            self.modes = self._default_modes()
        if not hasattr(self, "current_mode"):
            self.current_mode = "normal"

    def _init_default_providers(self):
        self.providers = {
            "zhipu": ProviderConfig(
                name="Zhipu",
                base_url="https://open.bigmodel.cn/api/coding/paas/v4/",
                api_key="",
                models=["glm-5.1", "glm-5"],
            ),
            "deepseek": ProviderConfig(
                name="DeepSeek",
                base_url="https://api.deepseek.com",
                api_key="",
                models=["deepseek-v4-pro", "deepseek-chat"],
            ),
        }

    def _default_modes(self) -> dict[str, ModeConfig]:
        return {
            "normal": ModeConfig(name="normal", auto_approve=False),
            "yolo": ModeConfig(
                name="yolo",
                auto_approve=True,
                dangerous_patterns=[
                    "rm ",
                    "rm\t",
                    "rmdir ",
                    "rd ",
                    "dd ",
                    "mv ",
                    "del ",
                    "copy ",
                    "xcopy ",
                    "chmod ",
                    "chown ",
                    "sudo ",
                    "format ",
                    "mkfs ",
                    "fdisk ",
                ],
            ),
        }

    # ---- Serialization ----

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """从字典创建配置"""
        config = cls()
        config._apply_dict(data)
        return config

    def _apply_dict(self, data: dict):
        if "current" in data:
            self.current = CurrentConfig(**data["current"])
        if "providers" in data:
            self.providers = {}
            for key, pdata in data["providers"].items():
                self.providers[key] = ProviderConfig(**pdata)
        if "max_tokens" in data:
            self.max_tokens = data["max_tokens"]
        if "permission" in data:
            self.permission = PermissionConfig.from_dict(data["permission"])
        if "tool_result_limit" in data:
            self.tool_result_limit = data["tool_result_limit"]
        if "tool_timeout" in data:
            self.tool_timeout = data["tool_timeout"]
        if "compact" in data:
            self.compact = CompactConfig(**data["compact"])
        if "dream" in data:
            self.dream = DreamConfig(**data["dream"])

    def to_dict(self) -> dict:
        """序列化为字典"""
        from dataclasses import asdict

        return {
            "current": asdict(self.current),
            "providers": {k: asdict(v) for k, v in self.providers.items()},
            "permission": self.permission.to_dict(),
            "max_tokens": self.max_tokens,
            "tool_result_limit": self.tool_result_limit,
            "tool_timeout": self.tool_timeout,
            "compact": asdict(self.compact),
            "dream": asdict(self.dream),
        }

    # ---- Convenience properties ----

    @property
    def current_provider(self) -> Optional[ProviderConfig]:
        return self.providers.get(self.current.provider)

    @property
    def model(self) -> str:
        return self.current.model

    @model.setter
    def model(self, value: str):
        self.current.model = value

    @property
    def api_key(self) -> str:
        provider = self.current_provider
        return provider.api_key if provider else ""

    @property
    def base_url(self) -> str:
        provider = self.current_provider
        return provider.base_url if provider else ""

    @property
    def models(self) -> list[str]:
        provider = self.current_provider
        return provider.models if provider else []

    @property
    def display_name(self) -> str:
        provider = self.current_provider
        name = provider.name if provider else self.current.provider
        return f"{self.current.model} ({name})"

    @property
    def extra_body(self) -> dict[str, Any] | None:
        provider = self.current_provider
        if provider and provider.extra_body:
            return provider.extra_body.get(self.model)
        return None

    # ---- Mode operations ----

    def set_mode(self, mode_name: str) -> bool:
        if mode_name in self.modes:
            self.current_mode = mode_name
            return True
        return False

    # ---- Mutation methods (caller responsible for persistence) ----

    def set_model(self, model: str, provider: str | None = None) -> bool:
        if provider:
            self.current.provider = provider
        self.current.model = model
        pconfig = self.current_provider
        if pconfig and model not in pconfig.models:
            pconfig.models.append(model)
        return provider is None or provider == self.current.provider

    def set_provider(self, provider_key: str, model: str | None = None) -> bool:
        if provider_key not in self.providers:
            raise ValueError(f"Unknown provider: {provider_key}")
        self.current.provider = provider_key
        pconfig = self.providers[provider_key]
        if model is None:
            model = pconfig.models[0] if pconfig.models else "default"
        if model not in pconfig.models:
            pconfig.models.append(model)
        self.current.model = model
        return True

    def add_provider(
        self,
        key: str,
        name: str,
        base_url: str,
        api_key: str = "",
        models: list[str] | None = None,
    ) -> None:
        if key in self.providers:
            raise ValueError(f"Provider '{key}' already exists")
        self.providers[key] = ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=api_key,
            models=models or [],
        )

    def add_model(self, model: str, provider: str | None = None) -> None:
        provider_key = provider or self.current.provider
        if provider_key not in self.providers:
            raise ValueError(f"Provider '{provider_key}' does not exist")
        pconfig = self.providers[provider_key]
        if model not in pconfig.models:
            pconfig.models.append(model)

    def remove_provider(self, key: str) -> str | None:
        if key not in self.providers:
            raise ValueError(f"Provider '{key}' does not exist")
        if len(self.providers) <= 1:
            raise ValueError("Cannot remove the last provider")
        new_current = None
        if self.current.provider == key:
            other_key = next(k for k in self.providers.keys() if k != key)
            self.current.provider = other_key
            pconfig = self.providers[other_key]
            self.current.model = pconfig.models[0] if pconfig.models else "default"
            new_current = other_key
        del self.providers[key]
        return new_current

    def remove_model(self, model: str, provider: str | None = None) -> str | None:
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
            remaining = [m for m in pconfig.models if m != model]
            new_model = remaining[0] if remaining else "default"
            self.current.model = new_model
        pconfig.models.remove(model)
        return new_model

    def update_provider(
        self,
        key: str,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        models: list[str] | None = None,
    ) -> bool:
        if key not in self.providers:
            raise ValueError(f"Provider '{key}' does not exist")
        pconfig = self.providers[key]
        if name is not None:
            pconfig.name = name
        if base_url is not None:
            pconfig.base_url = base_url
        if api_key is not None:
            pconfig.api_key = api_key
        if models is not None:
            pconfig.models = models
        return self.current.provider == key

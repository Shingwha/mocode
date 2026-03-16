"""配置管理 - 支持多供应商"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import json
import os


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
    provider: str = "openai"  # 供应商 key
    model: str = "gpt-4o"


@dataclass
class Config:
    """应用配置 - 支持多供应商"""

    current: CurrentConfig = field(default_factory=CurrentConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    max_tokens: int = 8192

    CONFIG_PATH: Path = Path.home() / ".nanocode" / "config.json"

    def __post_init__(self):
        """确保默认供应商存在"""
        if not self.providers:
            self._init_default_providers()

    def _init_default_providers(self):
        """初始化默认供应商"""
        self.providers = {
            "openai": ProviderConfig(
                name="OpenAI",
                base_url="https://api.openai.com/v1",
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini"],
            ),
            "longcat": ProviderConfig(
                name="LongCat",
                base_url="https://api.longcat.chat/openai",
                api_key="",
                models=["LongCat-Flash-Chat", "LongCat-Flash-Thinking", "LongCat-Flash-Lite"],
            ),
        }

    @classmethod
    def load(cls) -> "Config":
        """加载配置"""
        config = cls()

        if cls.CONFIG_PATH.exists():
            try:
                data = json.loads(cls.CONFIG_PATH.read_text(encoding="utf-8-sig"))

                # 加载当前配置
                if "current" in data:
                    config.current = CurrentConfig(**data["current"])

                # 加载供应商配置
                if "providers" in data:
                    for key, pdata in data["providers"].items():
                        config.providers[key] = ProviderConfig(**pdata)

                # 加载其他配置
                if "max_tokens" in data:
                    config.max_tokens = data["max_tokens"]

            except (json.JSONDecodeError, IOError, TypeError):
                pass

        # 环境变量覆盖 OpenAI key
        env_key = os.environ.get("OPENAI_API_KEY")
        if env_key and "openai" in config.providers:
            config.providers["openai"].api_key = env_key

        return config

    def save(self):
        """保存配置"""
        self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # 序列化
        data = {
            "current": asdict(self.current),
            "providers": {
                k: asdict(v) for k, v in self.providers.items()
            },
            "max_tokens": self.max_tokens,
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
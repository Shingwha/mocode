"""配置管理 - 支持多供应商"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any
import json
import os

from .permission import PermissionConfig
from ..paths import CONFIG_PATH


@dataclass
class RtkConfig:
    """RTK (Rust Token Killer) 配置"""
    enabled: bool = True  # 默认启用
    # RTK 支持的命令前缀
    commands: list[str] = field(default_factory=lambda: [
        "ls", "tree", "dir",
        "cat", "head", "tail",
        "find", "grep", "rg",
        "git status", "git log", "git diff", "git show",
        "cargo test", "cargo build", "cargo clippy",
        "npm test", "npm run", "yarn test",
        "pytest", "vitest", "jest",
        "docker", "kubectl",
        "tsc", "eslint", "ruff",
    ])


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
    permission: PermissionConfig = field(default_factory=PermissionConfig)
    max_tokens: int = 8192
    rtk: RtkConfig = field(default_factory=RtkConfig)

    CONFIG_PATH: Path = CONFIG_PATH

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
        env_key = os.environ.get("OPENAI_API_KEY")
        if env_key and "openai" in config.providers:
            config.providers["openai"].api_key = env_key

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

        # 加载 RTK 配置
        if "rtk" in data:
            self.rtk = RtkConfig(**data["rtk"])

    def save(self):
        """保存配置"""
        self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # 序列化
        data = {
            "current": asdict(self.current),
            "providers": {
                k: asdict(v) for k, v in self.providers.items()
            },
            "permission": self.permission.to_dict(),
            "max_tokens": self.max_tokens,
            "rtk": asdict(self.rtk),
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
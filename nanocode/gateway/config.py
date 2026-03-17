"""Gateway 配置"""

from dataclasses import dataclass, field
from typing import Any
import json
from pathlib import Path


@dataclass
class TelegramConfig:
    """Telegram 渠道配置"""
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = field(default_factory=list)


@dataclass
class GatewayConfig:
    """Gateway 总配置"""

    channels: dict[str, Any] = field(default_factory=dict)

    CONFIG_PATH: Path = Path.home() / ".nanocode" / "config.json"

    @classmethod
    def load(cls) -> "GatewayConfig":
        """从配置文件加载 Gateway 配置"""
        config = cls()

        if cls.CONFIG_PATH.exists():
            try:
                data = json.loads(cls.CONFIG_PATH.read_text(encoding="utf-8-sig"))
                if "gateway" in data and "channels" in data["gateway"]:
                    config.channels = data["gateway"]["channels"]
            except (json.JSONDecodeError, IOError):
                pass

        return config

    def get_telegram_config(self) -> TelegramConfig:
        """获取 Telegram 配置"""
        tg_data = self.channels.get("telegram", {})
        return TelegramConfig(
            enabled=tg_data.get("enabled", False),
            token=tg_data.get("token", ""),
            allow_from=tg_data.get("allowFrom", []),
        )

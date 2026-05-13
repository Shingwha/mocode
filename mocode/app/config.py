"""Config — pure data + load/save, no Store abstraction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".mocode" / "config.json"


@dataclass
class GatewayConfig:
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)

    def is_enabled(self, channel_name: str) -> bool:
        ch = self.channels.get(channel_name)
        return ch is not None and ch.get("enabled", False)

    def to_dict(self) -> dict[str, Any]:
        return {"channels": self.channels}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayConfig:
        return cls(channels=data.get("channels", {}))


@dataclass
class ImageConfig:
    enabled: bool = False
    base_url: str = "https://api.openai.com"
    api_key: str = ""
    model: str = "gpt-image-2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageConfig:
        return cls(
            enabled=data.get("enabled", False),
            base_url=data.get("base_url", "https://api.openai.com"),
            api_key=data.get("api_key", ""),
            model=data.get("model", "gpt-image-2"),
        )


@dataclass
class ProviderConfig:
    api_key: str
    model: str
    base_url: str | None = None
    extra_body: dict[str, Any] | None = None


@dataclass
class Config:
    provider: str
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    max_tokens: int = 8192
    tool_result_limit: int = 25000
    tool_timeout: int = 240
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    image: ImageConfig = field(default_factory=ImageConfig)

    def __post_init__(self):
        if isinstance(self.gateway, dict):
            self.gateway = GatewayConfig.from_dict(self.gateway)
        if isinstance(self.image, dict):
            self.image = ImageConfig.from_dict(self.image)

    @property
    def current(self) -> ProviderConfig | None:
        return self.providers.get(self.provider)

    @property
    def model(self) -> str:
        c = self.current
        return c.model if c else ""

    @property
    def api_key(self) -> str:
        c = self.current
        return c.api_key if c else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "providers": {k: asdict(v) for k, v in self.providers.items()},
            "max_tokens": self.max_tokens,
            "tool_result_limit": self.tool_result_limit,
            "tool_timeout": self.tool_timeout,
            "gateway": self.gateway.to_dict(),
            "image": self.image.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Parse config from dict. Handles both 0.2 nested format and 0.3 flat format."""
        current = data.get("current", {})
        provider_name = current.get("provider") or data.get("provider", "")
        active_model = current.get("model", "")

        providers = _parse_providers(data.get("providers", {}), provider_name, active_model)

        # Extract known scalar fields, ignore unknown (forward-compatible)
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {"provider": provider_name, "providers": providers}
        for key in ("max_tokens", "tool_result_limit", "tool_timeout"):
            if key in data and key in known:
                kwargs[key] = data[key]
        if "gateway" in data:
            kwargs["gateway"] = GatewayConfig.from_dict(data["gateway"])
        if "image" in data:
            kwargs["image"] = ImageConfig.from_dict(data["image"])

        return cls(**kwargs)

    # ---- Load / Save ----

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> Config | None:
        """Load config from JSON file. Returns None if file doesn't exist or is invalid."""
        p = Path(path)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def save(self, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
        """Save config to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def copy(self) -> Config:
        return Config.from_dict(self.to_dict())


def _parse_providers(
    raw: dict[str, Any],
    active_name: str,
    active_model: str,
) -> dict[str, ProviderConfig]:
    """Parse provider configs from raw dict. Handles 0.2 (models list, keyed extra_body) and 0.3 formats."""
    providers = {}
    for key, pdata in raw.items():
        # Resolve model: active > flat > first in list
        flat_model = pdata.get("model", "")
        models = pdata.get("models", [])
        if key == active_name and active_model:
            model = active_model
        elif flat_model:
            model = flat_model
        elif models:
            model = models[0]
        else:
            model = ""

        # Resolve extra_body: 0.2 keys by model name, 0.3 is flat
        raw_extra = pdata.get("extra_body")
        extra_body = None
        if isinstance(raw_extra, dict):
            extra_body = raw_extra.get(model) if model in raw_extra else (raw_extra or None)

        providers[key] = ProviderConfig(
            api_key=pdata.get("api_key", ""),
            model=model,
            base_url=pdata.get("base_url"),
            extra_body=extra_body,
        )
    return providers

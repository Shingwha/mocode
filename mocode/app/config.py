"""Config — pure data + load/save, no Store abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .utils import read_json, write_json

DEFAULT_CONFIG_PATH = Path.home() / ".mocode" / "config.json"


@dataclass
class ModelEntry:
    """A model and its optional extra_body."""

    name: str
    extra_body: dict[str, Any] | None = None


@dataclass
class ProviderEntry:
    """Complete configuration for a single provider."""

    name: str = ""
    api_key: str = ""
    base_url: str | None = None
    models: list[ModelEntry] = field(default_factory=list)

    def model_names(self) -> list[str]:
        return [m.name for m in self.models]

    def get_extra_body(self, model_name: str) -> dict[str, Any] | None:
        for m in self.models:
            if m.name == model_name:
                return m.extra_body
        return None


@dataclass
class Config:
    active_provider: str
    active_model: str
    providers: dict[str, ProviderEntry] = field(default_factory=dict)
    max_tokens: int = 8192
    tool_result_limit: int = 25000
    tool_timeout: int = 240

    @property
    def current(self) -> ProviderEntry | None:
        return self.providers.get(self.active_provider)

    @property
    def model(self) -> str:
        return self.active_model

    @property
    def api_key(self) -> str:
        entry = self.current
        return entry.api_key if entry else ""

    @property
    def extra_body(self) -> dict[str, Any] | None:
        entry = self.current
        return entry.get_extra_body(self.active_model) if entry else None

    def to_dict(self) -> dict[str, Any]:
        providers_out: dict[str, dict[str, Any]] = {}
        for key, entry in self.providers.items():
            models_out = []
            for m in entry.models:
                md: dict[str, Any] = {"name": m.name}
                if m.extra_body is not None:
                    md["extra_body"] = m.extra_body
                models_out.append(md)
            d: dict[str, Any] = {
                "name": entry.name,
                "api_key": entry.api_key,
                "models": models_out,
            }
            if entry.base_url is not None:
                d["base_url"] = entry.base_url
            providers_out[key] = d

        return {
            "active_provider": self.active_provider,
            "active_model": self.active_model,
            "providers": providers_out,
            "max_tokens": self.max_tokens,
            "tool_result_limit": self.tool_result_limit,
            "tool_timeout": self.tool_timeout,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        active_provider = data.get("active_provider", "")
        active_model = data.get("active_model", "")

        providers: dict[str, ProviderEntry] = {}
        for key, pdata in data.get("providers", {}).items():
            models = []
            for raw_m in pdata.get("models", []):
                if isinstance(raw_m, str):
                    models.append(ModelEntry(name=raw_m))
                elif isinstance(raw_m, dict):
                    models.append(
                        ModelEntry(
                            name=raw_m.get("name", ""),
                            extra_body=raw_m.get("extra_body"),
                        )
                    )
            providers[key] = ProviderEntry(
                name=pdata.get("name", ""),
                api_key=pdata.get("api_key", ""),
                base_url=pdata.get("base_url"),
                models=models,
            )

        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {
            "active_provider": active_provider,
            "active_model": active_model,
            "providers": providers,
        }
        for key in ("max_tokens", "tool_result_limit", "tool_timeout"):
            if key in data and key in known:
                kwargs[key] = data[key]

        return cls(**kwargs)

    # ---- Load / Save ----

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> Config | None:
        """Load config from JSON file. Returns None if file doesn't exist or is invalid."""
        data = read_json(path, encoding="utf-8-sig")
        if data is None:
            return None
        try:
            return cls.from_dict(data)
        except (KeyError, TypeError):
            return None

    def save(self, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
        """Save config to JSON file."""
        write_json(path, self.to_dict())

    def copy(self) -> Config:
        return Config.from_dict(self.to_dict())

"""Config — providers, models and host settings.

The file is organised by who owns each value::

    {
      "active_provider": "intern",
      "active_model": "Atria-Dawn-Preview",

      "agent": {                      # host execution policy, model-independent
        "tool_timeout": 240,
        "max_iterations": 0
      },

      "providers": {
        "intern": {
          "name": "Intern Discovery",
          "base_url": "https://discovery-api.intern-ai.org.cn/v1",
          "api_key": "sk-...",        # optional — see env_var_for()
          "models": {                 # keyed by model name
            "Atria-Dawn-Preview": {
              "context_window": 200000,
              "max_output": 32768,    # optional; no cap is sent when absent
              "extra_body": { }       # provider-specific request fields
            }
          }
        }
      },

      "plugins": { "shell": { "enabled": false } }
    }

Everything under a model is optional and stays absent unless configured:
MoCode never invents a model's limits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..core.provider import ModelSpec
from .io import read_json, write_json

DEFAULT_CONFIG_PATH = Path.home() / ".mocode" / "config.json"


def env_var_for(provider_key: str) -> str:
    """The environment variable holding *provider_key*'s API key.

    ``intern`` → ``INTERN_API_KEY``; ``my-gateway`` → ``MY_GATEWAY_API_KEY``.
    There is nothing to declare in the config file for this to work.
    """
    slug = "".join(c if c.isalnum() else "_" for c in provider_key).upper()
    return f"{slug}_API_KEY"


def _opt_int(value: Any, default: int | None = None) -> int | None:
    """*value* as an int, or *default* when it is absent or unparseable.

    An explicit ``None`` check, not ``or``: a configured 0 must survive.
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ModelEntry:
    """Per-model facts and provider-specific request fields."""

    context_window: int | None = None
    max_output: int | None = None
    extra_body: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ModelEntry:
        data = data or {}
        extra_body = data.get("extra_body")
        return cls(
            context_window=_opt_int(data.get("context_window")),
            max_output=_opt_int(data.get("max_output")),
            extra_body=extra_body if isinstance(extra_body, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.context_window is not None:
            out["context_window"] = self.context_window
        if self.max_output is not None:
            out["max_output"] = self.max_output
        if self.extra_body is not None:
            out["extra_body"] = self.extra_body
        return out


@dataclass
class ProviderEntry:
    """One provider: credentials, endpoint, and the models it serves."""

    name: str = ""
    api_key: str = ""
    base_url: str | None = None
    models: dict[str, ModelEntry] = field(default_factory=dict)

    def label(self, key: str) -> str:
        """Display name, falling back to the provider key."""
        return self.name or key

    def model_names(self) -> list[str]:
        return list(self.models)

    def api_key_for(self, key: str) -> str:
        """The explicit key if set, otherwise the conventional env var."""
        return self.api_key or os.environ.get(env_var_for(key), "")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderEntry:
        models = {
            str(name): ModelEntry.from_dict(raw)
            for name, raw in (data.get("models") or {}).items()
        }
        return cls(
            name=str(data.get("name") or ""),
            api_key=str(data.get("api_key") or ""),
            base_url=data.get("base_url") or None,
            models=models,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"models": {n: m.to_dict() for n, m in self.models.items()}}
        if self.name:
            out["name"] = self.name
        if self.api_key:
            out["api_key"] = self.api_key
        if self.base_url is not None:
            out["base_url"] = self.base_url
        return out


@dataclass
class AgentSettings:
    """Host execution policy — the same whatever model is loaded."""

    tool_timeout: int = 240
    max_iterations: int = 0  # 0 = unlimited


@dataclass
class Config:
    active_provider: str = ""
    active_model: str = ""
    agent: AgentSettings = field(default_factory=AgentSettings)
    providers: dict[str, ProviderEntry] = field(default_factory=dict)
    plugins: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Top-level keys MoCode does not own, carried through load → save untouched.
    foreign: dict[str, Any] = field(default_factory=dict, repr=False)
    #: Where this config was loaded from — saving goes back there.
    path: Path = field(default=DEFAULT_CONFIG_PATH, repr=False, compare=False)

    _OWNED_KEYS: ClassVar[tuple[str, ...]] = (
        "active_provider",
        "active_model",
        "agent",
        "providers",
        "plugins",
    )

    # ── Queries ────────────────────────────────────────────

    @property
    def current(self) -> ProviderEntry | None:
        return self.providers.get(self.active_provider)

    def model_spec(
        self, provider_key: str | None = None, model_name: str | None = None
    ) -> ModelSpec:
        """Resolve the facts of a provider/model pair into a ModelSpec.

        Unknown models resolve to a bare spec — no invented limits.
        """
        key = self.active_provider if provider_key is None else provider_key
        name = self.active_model if model_name is None else model_name
        entry = self.providers.get(key)
        model = entry.models.get(name) if entry else None
        if model is None:
            return ModelSpec(name=name)
        return ModelSpec(
            name=name,
            context_window=model.context_window,
            max_output=model.max_output,
        )

    # ── Serialization ──────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "active_provider": self.active_provider,
            "active_model": self.active_model,
            "agent": {
                "tool_timeout": self.agent.tool_timeout,
                "max_iterations": self.agent.max_iterations,
            },
            "providers": {key: entry.to_dict() for key, entry in self.providers.items()},
            "plugins": self.plugins,
        }
        for key, value in self.foreign.items():
            data.setdefault(key, value)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        agent_raw = data.get("agent") or {}
        defaults = AgentSettings()
        return cls(
            active_provider=str(data.get("active_provider") or ""),
            active_model=str(data.get("active_model") or ""),
            agent=AgentSettings(
                tool_timeout=_opt_int(
                    agent_raw.get("tool_timeout"), defaults.tool_timeout
                ),
                max_iterations=_opt_int(agent_raw.get("max_iterations"), 0),
            ),
            providers={
                str(key): ProviderEntry.from_dict(raw or {})
                for key, raw in (data.get("providers") or {}).items()
            },
            plugins=data.get("plugins") or {},
            foreign={k: v for k, v in data.items() if k not in cls._OWNED_KEYS},
        )

    # ── Load / save ────────────────────────────────────────

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> Config | None:
        """Load config from JSON. Returns ``None`` if missing or unreadable."""
        data = read_json(path, encoding="utf-8-sig")
        if data is None:
            return None
        try:
            config = cls.from_dict(data)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        config.path = Path(path)
        return config

    def save(self, path: Path | str | None = None) -> None:
        """Write the config back to where it was loaded from (or *path*)."""
        write_json(path or self.path, self.to_dict())

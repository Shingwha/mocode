"""Config — providers, models and host settings.

The file is organised by who owns each value::

    {
      "provider": "commandcode",               // the default for new conversations
      "model": "deepseek/deepseek-v4.1-flash",

      "agent": {                      # loop execution policy — the core AgentConfig
        "tool_timeout": 240,          # seconds per tool call
        "max_iterations": 0,          # 0 = unlimited; per-turn budgets
        "max_tool_calls": 0,
        "max_turn_seconds": 0,
        "tool_result_limit": 50000    # chars, per tool result
      },

      "providers": {
        "commandcode": {
          "type": "openai",           # optional; "openai" is the built-in default
          "name": "Command Code",     # optional display name
          "base_url": "https://api.commandcode.ai/provider/v1",
          "api_key": "sk-...",        # optional — see env_var_for()
          "models": [                 # ordered array; "id" is the unique key
            {
              "id": "deepseek/deepseek-v4.1-flash",
              "name": "DeepSeek V4.1 Flash",  # optional display name
              "context_window": 1000000,      # optional = unknown
              "max_tokens": 65536,            # optional; no cap is sent when absent
              "efforts": ["low", "high", "max"],  # optional level table; the
                                                  # kernel default is low/medium/high
              "effort": "high",               # optional level sent with the request;
                                              # absent = the server decides
              "retry": {              # optional; RetryPolicy fields, unknown keys ignored
                "max_attempts": 3,
                "base_delay": 5.0
              }
            }
          ]
        }
      },

      "plugins": { "shell": { "enabled": false } }
    }

Everything under a model is optional and stays absent unless configured:
MoCode never invents a model's limits.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

from ..core.agent import AgentConfig
from ..core.provider import EFFORTS, ModelSpec, RetryPolicy
from .io import read_json, write_json

DEFAULT_CONFIG_PATH = Path.home() / ".mocode" / "config.json"

#: The keys a model entry's ``retry`` sub-object may carry — the RetryPolicy
#: fields, so an unknown key is dropped at load (forward compatibility, the
#: same rule every other block here practices) instead of exploding a
#: constructor later.
_RETRY_KEYS = frozenset(f.name for f in fields(RetryPolicy))


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
    """Per-model facts: identity, limits, the effort table, a retry override.

    ``efforts`` is the model's ordered reasoning-level table — ``None`` means
    the file declares none and the kernel default (``EFFORTS``) stands at
    resolve time. The series is open: any custom level names are allowed and
    a provider sends the level verbatim. ``effort`` is the level sent with
    the request — ``None`` means the request carries no such parameter and
    the server decides entirely on its own.
    """

    id: str = ""
    name: str = ""
    context_window: int | None = None
    max_tokens: int | None = None
    #: ``None`` = the file declares no custom table, not an empty one.
    efforts: tuple[str, ...] | None = None
    effort: str | None = None
    #: Per-model retry override — a dict of :class:`RetryPolicy
    #: <mocode.core.provider.RetryPolicy>` fields. Rate limits are knowledge
    #: about a backend, and two models behind one provider key can disagree;
    #: ``None`` means the provider's own policy stands.
    retry: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ModelEntry:
        data = data or {}
        retry = data.get("retry")
        efforts = data.get("efforts")
        if isinstance(efforts, list):
            # Item-wise str: a non-string entry is dropped, and a list that
            # ends up empty means "not declared", exactly like a missing key.
            efforts = tuple(e for e in efforts if isinstance(e, str)) or None
        else:
            efforts = None
        effort = data.get("effort")
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            context_window=_opt_int(data.get("context_window")),
            max_tokens=_opt_int(data.get("max_tokens")),
            efforts=efforts,
            effort=effort if isinstance(effort, str) else None,
            retry=(
                {k: v for k, v in retry.items() if k in _RETRY_KEYS}
                if isinstance(retry, dict) and retry
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        if self.name:
            out["name"] = self.name
        if self.context_window is not None:
            out["context_window"] = self.context_window
        if self.max_tokens is not None:
            out["max_tokens"] = self.max_tokens
        if self.efforts is not None:
            out["efforts"] = list(self.efforts)
        if self.effort is not None:
            out["effort"] = self.effort
        if self.retry:
            out["retry"] = dict(self.retry)
        return out

    def retry_policy(self) -> RetryPolicy | None:
        """The override as a :class:`RetryPolicy` — ``None`` when not set.

        The dict was filtered to the known fields at load, so the splat is
        safe by construction. A factory hands this to its provider's
        ``retry_policy`` parameter; absent means the provider keeps its own.
        """
        return RetryPolicy(**self.retry) if self.retry else None


@dataclass
class ProviderEntry:
    """One provider: its implementation type, credentials, endpoint, models.

    ``type`` selects the implementation a :class:`~mocode.host.runtime.MoCode`
    runtime builds for it — ``"openai"`` ships built in; anything else must
    have been registered with ``MoCode.register_provider_type`` first. A
    missing ``type`` means ``"openai"``, so configurations written before the
    field existed keep working. ``models`` is an ordered array — declaration
    order is the order a frontend's selector shows.
    """

    type: str = "openai"
    name: str = ""
    api_key: str = ""
    base_url: str | None = None
    models: list[ModelEntry] = field(default_factory=list)

    def label(self, key: str) -> str:
        """Display name, falling back to the provider key."""
        return self.name or key

    def model_ids(self) -> list[str]:
        """The declared model ids, in declaration order."""
        return [m.id for m in self.models]

    def model_names(self) -> list[str]:
        """Bridge for the pre-rename CLI call sites that W3 rewrites."""
        return self.model_ids()

    def model(self, model_id: str) -> ModelEntry | None:
        """The entry declared for *model_id* — ``None`` when not declared."""
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    def api_key_for(self, key: str) -> str:
        """The explicit key if set, otherwise the conventional env var."""
        return self.api_key or os.environ.get(env_var_for(key), "")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderEntry:
        models: list[ModelEntry] = []
        raw_models = data.get("models")
        if isinstance(raw_models, list):
            for raw in raw_models:
                if not isinstance(raw, dict):
                    continue
                model = ModelEntry.from_dict(raw)
                if not model.id:
                    continue  # an entry without an id cannot be addressed
                models.append(model)
        return cls(
            type=str(data.get("type") or "openai"),
            name=str(data.get("name") or ""),
            api_key=str(data.get("api_key") or ""),
            base_url=data.get("base_url") or None,
            models=models,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"models": [m.to_dict() for m in self.models]}
        if self.type != "openai":
            out["type"] = self.type
        if self.name:
            out["name"] = self.name
        if self.api_key:
            out["api_key"] = self.api_key
        if self.base_url is not None:
            out["base_url"] = self.base_url
        return out


@dataclass
class Config:
    provider: str = ""
    model: str = ""
    #: Loop execution policy — the *only* policy type, owned by core: the
    #: same AgentConfig the loop runs under, nested-(de)serialized here. One
    #: type means every field (the budgets, the result limit) is configurable
    #: the moment it exists, with no hand-maintained mapping to drift.
    agent: AgentConfig = field(default_factory=AgentConfig)
    providers: dict[str, ProviderEntry] = field(default_factory=dict)
    plugins: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Top-level keys MoCode does not own, carried through load → save untouched.
    foreign: dict[str, Any] = field(default_factory=dict, repr=False)
    #: Where this config was loaded from — saving goes back there.
    path: Path = field(default=DEFAULT_CONFIG_PATH, repr=False, compare=False)

    _OWNED_KEYS: ClassVar[tuple[str, ...]] = (
        "provider",
        "model",
        "agent",
        "providers",
        "plugins",
    )

    # ── Queries ────────────────────────────────────────────

    @property
    def current(self) -> ProviderEntry | None:
        return self.providers.get(self.provider)

    def model_spec(
        self, provider_key: str | None = None, model_name: str | None = None
    ) -> ModelSpec:
        """Resolve the facts of a provider/model pair into a ModelSpec.

        Unknown models resolve to a bare spec — no invented limits. A model
        without a declared ``efforts`` table gets the kernel default series;
        a declared one passes through verbatim.
        """
        key = self.provider if provider_key is None else provider_key
        name = self.model if model_name is None else model_name
        entry = self.providers.get(key)
        model = entry.model(name) if entry else None
        if model is None:
            return ModelSpec(name=name)
        return ModelSpec(
            name=name,
            context_window=model.context_window,
            max_tokens=model.max_tokens,
            efforts=model.efforts or EFFORTS,
            effort=model.effort,
        )

    # ── Serialization ──────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "agent": asdict(self.agent),
            "providers": {key: entry.to_dict() for key, entry in self.providers.items()},
            "plugins": self.plugins,
        }
        for key, value in self.foreign.items():
            data.setdefault(key, value)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        agent_raw = data.get("agent")
        if not isinstance(agent_raw, dict):
            agent_raw = {}
        defaults = AgentConfig()
        # Known fields only: an unknown subkey is ignored (not an error), the
        # same forward-compatibility every other block here practices.
        agent = AgentConfig(
            **{
                f.name: _opt_int(agent_raw[f.name], getattr(defaults, f.name))
                for f in fields(AgentConfig)
                if f.name in agent_raw
            }
        )
        return cls(
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            agent=agent,
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

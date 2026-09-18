"""Tests for mocode.host.config — providers, models, settings, load/save."""

import json

import pytest

from mocode.host.config import (
    AgentSettings,
    Config,
    ModelEntry,
    ProviderEntry,
    env_var_for,
)


class TestEnvVarFor:
    @pytest.mark.parametrize(
        "provider_key,expected",
        [
            ("intern", "INTERN_API_KEY"),
            ("my-gateway", "MY_GATEWAY_API_KEY"),
            ("OpenAI", "OPENAI_API_KEY"),
            ("a.b", "A_B_API_KEY"),
        ],
    )
    def test_conventional_name(self, provider_key, expected):
        assert env_var_for(provider_key) == expected


class TestModelEntry:
    def test_defaults_are_all_unset(self):
        model = ModelEntry()
        assert model.context_window is None
        assert model.max_output is None
        assert model.extra_body is None

    def test_roundtrip_omits_unset_fields(self):
        assert ModelEntry().to_dict() == {}
        assert ModelEntry.from_dict(None) == ModelEntry()
        assert ModelEntry.from_dict({}) == ModelEntry()

    def test_roundtrip(self):
        model = ModelEntry(context_window=200_000, max_output=32_768, extra_body={"a": 1})
        assert ModelEntry.from_dict(model.to_dict()) == model

    def test_accepts_numeric_strings(self):
        model = ModelEntry.from_dict({"context_window": "128000", "max_output": "8192"})
        assert model.context_window == 128_000
        assert model.max_output == 8_192

    def test_garbage_limits_become_unset(self):
        model = ModelEntry.from_dict({"context_window": "huge", "max_output": True})
        assert model.context_window is None
        assert model.max_output is None


class TestProviderEntry:
    def test_defaults(self):
        entry = ProviderEntry()
        assert entry.name == ""
        assert entry.base_url is None
        assert entry.models == {}
        assert entry.model_names() == []

    def test_label_falls_back_to_key(self):
        assert ProviderEntry().label("deepseek") == "deepseek"
        assert ProviderEntry(name="DeepSeek").label("deepseek") == "DeepSeek"

    def test_models_are_keyed_by_name(self):
        entry = ProviderEntry.from_dict({"models": {"a": {}, "b": {"max_output": 100}}})
        assert entry.model_names() == ["a", "b"]
        assert entry.models["b"].max_output == 100

    def test_null_model_value_means_no_settings(self):
        entry = ProviderEntry.from_dict({"models": {"a": None}})
        assert entry.models["a"] == ModelEntry()

    def test_explicit_key_wins(self, monkeypatch):
        monkeypatch.setenv("DEMO_API_KEY", "from-env")
        assert ProviderEntry(api_key="explicit").api_key_for("demo") == "explicit"

    def test_falls_back_to_environment(self, monkeypatch):
        monkeypatch.setenv("DEMO_API_KEY", "from-env")
        assert ProviderEntry().api_key_for("demo") == "from-env"

    def test_missing_key_resolves_empty(self, monkeypatch):
        monkeypatch.delenv("DEMO_API_KEY", raising=False)
        assert ProviderEntry().api_key_for("demo") == ""

    def test_roundtrip(self):
        entry = ProviderEntry(
            name="Demo",
            api_key="sk-1",
            base_url="https://example.test/v1",
            models={"m": ModelEntry(max_output=512)},
        )
        assert ProviderEntry.from_dict(entry.to_dict()) == entry


class TestConfigModelSpec:
    def _config(self) -> Config:
        return Config(
            active_provider="demo",
            active_model="big",
            providers={
                "demo": ProviderEntry(
                    models={
                        "big": ModelEntry(context_window=200_000, max_output=32_768),
                        "bare": ModelEntry(),
                    }
                )
            },
        )

    def test_resolves_active_pair(self):
        spec = self._config().model_spec()
        assert spec.name == "big"
        assert spec.context_window == 200_000
        assert spec.max_output == 32_768

    def test_resolves_explicit_pair(self):
        spec = self._config().model_spec("demo", "bare")
        assert spec.name == "bare"
        assert spec.context_window is None
        assert spec.max_output is None

    def test_unknown_model_gets_no_invented_limits(self):
        spec = self._config().model_spec("demo", "who-knows")
        assert spec.name == "who-knows"
        assert spec.max_output is None

    def test_unknown_provider_gets_no_invented_limits(self):
        spec = self._config().model_spec("ghost", "big")
        assert spec.name == "big"
        assert spec.context_window is None

    def test_is_configured(self):
        config = self._config()
        assert config.is_configured()
        assert config.is_configured("bare")
        assert not config.is_configured("who-knows")


class TestConfigSerialization:
    def test_agent_settings_defaults(self):
        config = Config.from_dict({})
        assert config.agent == AgentSettings()
        assert config.agent.tool_timeout == 240
        assert config.agent.max_iterations == 0

    def test_agent_settings_override(self):
        config = Config.from_dict({"agent": {"tool_timeout": 30, "max_iterations": 5}})
        assert config.agent.tool_timeout == 30
        assert config.agent.max_iterations == 5

    def test_roundtrip(self):
        original = Config(
            active_provider="demo",
            active_model="m",
            agent=AgentSettings(tool_timeout=45, max_iterations=7),
            providers={"demo": ProviderEntry(api_key="sk-1", models={"m": ModelEntry(max_output=1024)})},
            plugins={"shell": {"enabled": False}},
        )
        assert Config.from_dict(original.to_dict()) == original

    def test_foreign_keys_survive_roundtrip(self):
        """Keys MoCode does not own must not be dropped when it saves."""
        raw = {
            "active_provider": "demo",
            "active_model": "m",
            "providers": {},
            "active_theme": "mist",
            "tools": {"websearch": {"apiKey": "as_sk_x"}},
        }
        config = Config.from_dict(raw)
        assert config.foreign == {
            "active_theme": "mist",
            "tools": {"websearch": {"apiKey": "as_sk_x"}},
        }
        assert config.to_dict()["active_theme"] == "mist"
        assert config.to_dict()["tools"] == {"websearch": {"apiKey": "as_sk_x"}}

    def test_owned_keys_win_over_foreign(self):
        config = Config.from_dict({"active_model": "real", "providers": {}})
        config.foreign["active_model"] = "stale"
        assert config.to_dict()["active_model"] == "real"

    def test_copy_is_independent(self):
        config = Config(active_model="a", providers={"p": ProviderEntry(api_key="k")})
        clone = config.copy()
        clone.providers["p"].api_key = "changed"
        assert config.providers["p"].api_key == "k"

    def test_api_key_and_extra_body_of_active_model(self):
        config = Config(
            active_provider="demo",
            active_model="m",
            providers={
                "demo": ProviderEntry(
                    api_key="sk-1", models={"m": ModelEntry(extra_body={"x": 1})}
                )
            },
        )
        assert config.api_key == "sk-1"
        assert config.extra_body == {"x": 1}

    def test_missing_active_provider_is_survivable(self):
        config = Config.from_dict({"active_provider": "ghost", "providers": {}})
        assert config.current is None
        assert config.api_key == ""
        assert config.extra_body is None


class TestConfigPersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "config.json"
        original = Config(
            active_provider="demo",
            active_model="m",
            providers={"demo": ProviderEntry(api_key="sk-1", models={"m": ModelEntry()})},
        )
        original.save(path)
        loaded = Config.load(path)
        assert loaded is not None
        assert loaded.active_provider == "demo"
        assert loaded.providers["demo"].models["m"] == ModelEntry()

    def test_load_remembers_its_path(self, tmp_path):
        path = tmp_path / "custom.json"
        Config(active_model="m").save(path)
        loaded = Config.load(path)
        assert loaded.path == path
        loaded.save()  # no path argument → back to where it came from
        assert json.loads(path.read_text(encoding="utf-8"))["active_model"] == "m"

    def test_load_missing_file_returns_none(self, tmp_path):
        assert Config.load(tmp_path / "nope.json") is None

    def test_load_invalid_json_returns_none(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert Config.load(path) is None

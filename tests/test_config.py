"""Tests for mocode.host.config — providers, models, settings, load/save."""

import json

import pytest

from mocode.core.agent import AgentConfig
from mocode.core.provider import EFFORTS, RetryPolicy
from mocode.host.config import (
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
        assert model.id == ""
        assert model.name == ""
        assert model.context_window is None
        assert model.max_tokens is None
        assert model.efforts is None
        assert model.effort is None
        assert model.retry is None

    def test_roundtrip_omits_unset_fields_but_keeps_the_id(self):
        assert ModelEntry().to_dict() == {"id": ""}
        assert ModelEntry.from_dict(None) == ModelEntry()
        assert ModelEntry.from_dict({}) == ModelEntry()

    def test_roundtrip(self):
        model = ModelEntry(
            id="big",
            name="Big",
            context_window=200_000,
            max_tokens=32_768,
            efforts=("low", "high", "max"),
            effort="high",
        )
        assert ModelEntry.from_dict(model.to_dict()) == model

    def test_accepts_numeric_strings(self):
        model = ModelEntry.from_dict({"context_window": "128000", "max_tokens": "8192"})
        assert model.context_window == 128_000
        assert model.max_tokens == 8_192

    def test_garbage_limits_become_unset(self):
        model = ModelEntry.from_dict({"context_window": "huge", "max_tokens": True})
        assert model.context_window is None
        assert model.max_tokens is None

    def test_id_and_name_accept_only_strings(self):
        model = ModelEntry.from_dict({"id": 42, "name": None})
        assert model.id == "42"
        assert model.name == ""

    def test_efforts_accept_a_list_of_strings(self):
        model = ModelEntry.from_dict({"efforts": ["high", "xhigh", "max"]})
        assert model.efforts == ("high", "xhigh", "max")
        assert model.to_dict()["efforts"] == ["high", "xhigh", "max"]

    def test_efforts_drop_non_string_items(self):
        model = ModelEntry.from_dict({"efforts": ["high", 1, None, "max"]})
        assert model.efforts == ("high", "max")

    def test_efforts_empty_list_means_undeclared(self):
        assert ModelEntry.from_dict({"efforts": []}).efforts is None
        assert ModelEntry.from_dict({"efforts": [1, None]}).efforts is None

    def test_efforts_non_list_means_undeclared(self):
        for raw in ("high", {"low": 1}, 3):
            assert ModelEntry.from_dict({"efforts": raw}).efforts is None

    def test_effort_accepts_only_a_string(self):
        assert ModelEntry.from_dict({"effort": "low"}).effort == "low"
        for raw in (1, True, ["low"]):
            assert ModelEntry.from_dict({"effort": raw}).effort is None

    def test_retry_roundtrips_and_drops_unknown_keys(self):
        model = ModelEntry.from_dict(
            {"retry": {"max_attempts": 3, "base_delay": 5.0, "bogus": 1}}
        )
        assert model.retry == {"max_attempts": 3, "base_delay": 5.0}
        assert ModelEntry.from_dict(model.to_dict()).retry == model.retry

    def test_an_empty_or_non_dict_retry_is_unset(self):
        assert ModelEntry.from_dict({"retry": {}}).retry is None
        assert ModelEntry.from_dict({"retry": "fast"}).retry is None

    def test_retry_policy_builds_from_the_dict_or_stays_none(self):
        policy = ModelEntry.from_dict(
            {"retry": {"max_attempts": 3, "base_delay": 5.0}}
        ).retry_policy()
        assert policy == RetryPolicy(max_attempts=3, base_delay=5.0)
        assert ModelEntry().retry_policy() is None


class TestProviderEntry:
    def test_defaults(self):
        entry = ProviderEntry()
        assert entry.name == ""
        assert entry.base_url is None
        assert entry.models == []
        assert entry.model_ids() == []

    def test_label_falls_back_to_key(self):
        assert ProviderEntry().label("deepseek") == "deepseek"
        assert ProviderEntry(name="DeepSeek").label("deepseek") == "DeepSeek"

    def test_models_parse_as_an_ordered_array(self):
        entry = ProviderEntry.from_dict(
            {"models": [{"id": "a"}, {"id": "b", "max_tokens": 100}]}
        )
        assert entry.model_ids() == ["a", "b"]
        assert entry.model("b").max_tokens == 100

    def test_entries_without_an_id_are_skipped(self):
        entry = ProviderEntry.from_dict(
            {"models": [{"id": "a"}, {"name": "no id"}, {}]}
        )
        assert entry.model_ids() == ["a"]

    def test_non_dict_entries_are_skipped(self):
        entry = ProviderEntry.from_dict({"models": [{"id": "a"}, "b", None, 3]})
        assert entry.model_ids() == ["a"]

    def test_non_list_models_means_none_at_all(self):
        entry = ProviderEntry.from_dict({"models": {"a": {}}})
        assert entry.models == []

    def test_model_lookup_hits_and_misses(self):
        entry = ProviderEntry(models=[ModelEntry(id="a"), ModelEntry(id="b")])
        assert entry.model("b").id == "b"
        assert entry.model("who-knows") is None

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
            models=[ModelEntry(id="m", max_tokens=512)],
        )
        assert ProviderEntry.from_dict(entry.to_dict()) == entry

    def test_an_entry_without_settings_serializes_its_id_only(self):
        entry = ProviderEntry(models=[ModelEntry(id="m")])
        assert entry.to_dict() == {"models": [{"id": "m"}]}


class TestConfigModelSpec:
    def _config(self) -> Config:
        return Config(
            provider="demo",
            model="big",
            providers={
                "demo": ProviderEntry(
                    models=[
                        ModelEntry(id="big", context_window=200_000, max_tokens=32_768),
                        ModelEntry(id="bare"),
                        ModelEntry(
                            id="custom",
                            efforts=("high", "xhigh", "max"),
                            effort="xhigh",
                        ),
                    ]
                )
            },
        )

    def test_resolves_active_pair(self):
        spec = self._config().model_spec()
        assert spec.name == "big"
        assert spec.context_window == 200_000
        assert spec.max_tokens == 32_768

    def test_resolves_explicit_pair(self):
        spec = self._config().model_spec("demo", "bare")
        assert spec.name == "bare"
        assert spec.context_window is None
        assert spec.max_tokens is None

    def test_unknown_model_gets_no_invented_limits(self):
        spec = self._config().model_spec("demo", "who-knows")
        assert spec.name == "who-knows"
        assert spec.max_tokens is None

    def test_unknown_provider_gets_no_invented_limits(self):
        spec = self._config().model_spec("ghost", "big")
        assert spec.name == "big"
        assert spec.context_window is None

    def test_efforts_fall_back_to_the_kernel_default(self):
        assert self._config().model_spec("demo", "big").efforts == EFFORTS
        assert self._config().model_spec("demo", "who-knows").efforts == EFFORTS
        assert self._config().model_spec("ghost", "big").efforts == EFFORTS

    def test_custom_efforts_pass_through_verbatim(self):
        spec = self._config().model_spec("demo", "custom")
        assert spec.efforts == ("high", "xhigh", "max")
        assert spec.effort == "xhigh"

    def test_undeclared_effort_stays_absent(self):
        assert self._config().model_spec("demo", "big").effort is None


class TestConfigSerialization:
    def test_agent_block_defaults(self):
        config = Config.from_dict({})
        assert config.agent == AgentConfig()
        assert config.agent.tool_timeout == 240
        assert config.agent.max_iterations == 0
        assert config.agent.max_tool_calls == 0
        assert config.agent.max_turn_seconds == 0
        assert config.agent.tool_result_limit == 50000

    def test_agent_block_is_the_core_policy_type(self):
        """One policy type: what the loop runs under is what the file stores."""
        config = Config.from_dict(
            {"agent": {"tool_timeout": 30, "max_turn_seconds": 120, "tool_result_limit": 9000}}
        )
        assert config.agent == AgentConfig(
            tool_timeout=30, max_turn_seconds=120, tool_result_limit=9000
        )

    def test_agent_block_roundtrips_every_field(self):
        original = Config(agent=AgentConfig(tool_timeout=45, max_iterations=7, max_tool_calls=99))
        assert Config.from_dict(original.to_dict()).agent == original.agent

    def test_unknown_agent_subkeys_are_ignored(self):
        config = Config.from_dict({"agent": {"tool_timeout": 30, "mystery": True}})
        assert config.agent.tool_timeout == 30
        assert not hasattr(config.agent, "mystery")

    def test_garbage_agent_values_fall_back_to_defaults(self):
        config = Config.from_dict({"agent": {"tool_timeout": "soon", "max_iterations": None}})
        assert config.agent.tool_timeout == AgentConfig().tool_timeout
        assert config.agent.max_iterations == AgentConfig().max_iterations

    def test_a_non_dict_agent_section_is_survivable(self):
        config = Config.from_dict({"agent": None, "model": "m"})
        assert config.agent == AgentConfig()

    def test_top_level_keys_are_provider_and_model(self):
        config = Config.from_dict({"provider": "demo", "model": "m"})
        assert config.provider == "demo"
        assert config.model == "m"
        assert config.to_dict()["provider"] == "demo"
        assert config.to_dict()["model"] == "m"

    def test_roundtrip(self):
        original = Config(
            provider="demo",
            model="m",
            agent=AgentConfig(tool_timeout=45, max_iterations=7),
            providers={
                "demo": ProviderEntry(
                    api_key="sk-1",
                    models=[ModelEntry(id="m", max_tokens=1024)],
                )
            },
            plugins={"shell": {"enabled": False}},
        )
        assert Config.from_dict(original.to_dict()) == original

    def test_foreign_keys_survive_roundtrip(self):
        """Keys MoCode does not own must not be dropped when it saves."""
        raw = {
            "provider": "demo",
            "model": "m",
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
        config = Config.from_dict({"model": "real", "providers": {}})
        config.foreign["model"] = "stale"
        assert config.to_dict()["model"] == "real"

    def test_unset_keys_stay_absent(self):
        config = Config(providers={"demo": ProviderEntry()})
        data = config.to_dict()
        assert data["provider"] == ""
        assert data["model"] == ""
        assert data["providers"] == {"demo": {"models": []}}
        assert "plugins" in data  # an empty mapping is still owned, not omitted

    def test_missing_provider_is_survivable(self):
        config = Config.from_dict({"provider": "ghost", "providers": {}})
        assert config.current is None


class TestConfigPersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "config.json"
        original = Config(
            provider="demo",
            model="m",
            providers={
                "demo": ProviderEntry(api_key="sk-1", models=[ModelEntry(id="m")])
            },
        )
        original.save(path)
        loaded = Config.load(path)
        assert loaded is not None
        assert loaded.provider == "demo"
        assert loaded.providers["demo"].model("m") == ModelEntry(id="m")

    def test_load_remembers_its_path(self, tmp_path):
        path = tmp_path / "custom.json"
        Config(model="m").save(path)
        loaded = Config.load(path)
        assert loaded.path == path
        loaded.save()  # no path argument → back to where it came from
        assert json.loads(path.read_text(encoding="utf-8"))["model"] == "m"

    def test_load_missing_file_returns_none(self, tmp_path):
        assert Config.load(tmp_path / "nope.json") is None

    def test_load_invalid_json_returns_none(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert Config.load(path) is None

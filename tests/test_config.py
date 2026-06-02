"""Tests for mocode.app.config — Config, ProviderConfig, ProviderInfo, load/save."""

import json

import pytest

from mocode.app.config import Config, ProviderConfig, ProviderInfo


class TestProviderConfig:
    def test_create(self):
        pc = ProviderConfig(api_key="sk-test", model="gpt-4o")
        assert pc.api_key == "sk-test"
        assert pc.model == "gpt-4o"
        assert pc.base_url is None
        assert pc.extra_body is None

    def test_with_all_fields(self):
        pc = ProviderConfig(
            api_key="sk-test",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            extra_body={"thinking": {"type": "enabled"}},
        )
        assert pc.base_url == "https://api.deepseek.com"
        assert pc.extra_body == {"thinking": {"type": "enabled"}}


class TestConfig:
    def test_create(self):
        c = Config(
            provider="deepseek",
            providers={
                "deepseek": ProviderConfig(api_key="sk-test", model="deepseek-chat"),
            },
        )
        assert c.provider == "deepseek"
        assert len(c.providers) == 1
        assert c.max_tokens == 8192

    def test_current_property(self):
        pc = ProviderConfig(api_key="sk-test", model="gpt-4o")
        c = Config(provider="openai", providers={"openai": pc})
        assert c.current is pc

    def test_current_missing_provider(self):
        c = Config(provider="missing", providers={})
        assert c.current is None

    def test_to_dict_roundtrip(self):
        c = Config(
            provider="deepseek",
            providers={
                "deepseek": ProviderConfig(
                    api_key="sk-test",
                    model="deepseek-chat",
                    base_url="https://api.deepseek.com",
                    extra_body={"thinking": {"type": "enabled"}},
                ),
                "openai": ProviderConfig(api_key="sk-openai", model="gpt-4o"),
            },
            max_tokens=4096,
            tool_result_limit=10000,
            tool_timeout=120,
        )
        d = c.to_dict()
        c2 = Config.from_dict(d)
        assert c2.provider == c.provider
        assert c2.max_tokens == c.max_tokens
        assert c2.tool_result_limit == c.tool_result_limit
        assert c2.tool_timeout == c.tool_timeout
        assert len(c2.providers) == 2
        assert c2.providers["deepseek"].api_key == "sk-test"
        assert c2.providers["deepseek"].base_url == "https://api.deepseek.com"
        assert c2.providers["deepseek"].extra_body == {"thinking": {"type": "enabled"}}
        assert c2.providers["openai"].model == "gpt-4o"

    def test_copy_independent(self):
        c = Config(
            provider="deepseek",
            providers={
                "deepseek": ProviderConfig(api_key="sk-test", model="deepseek-chat"),
            },
        )
        c2 = c.copy()
        c2.provider = "openai"
        c2.providers["deepseek"].api_key = "changed"
        assert c.provider == "deepseek"
        assert c.providers["deepseek"].api_key == "sk-test"

    def test_from_dict_defaults(self):
        c = Config.from_dict({"provider": "x", "providers": {}})
        assert c.max_tokens == 8192
        assert c.tool_result_limit == 25000
        assert c.tool_timeout == 240


class TestConfigPersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "config.json"
        c = Config(
            provider="deepseek",
            providers={
                "deepseek": ProviderConfig(api_key="sk-test", model="deepseek-chat"),
            },
        )
        c.save(path)
        assert path.exists()

        c2 = Config.load(path)
        assert c2 is not None
        assert c2.provider == "deepseek"
        assert c2.providers["deepseek"].api_key == "sk-test"

    def test_load_nonexistent(self, tmp_path):
        path = tmp_path / "nope.json"
        assert Config.load(path) is None

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert Config.load(path) is None


class TestProviderInfo:
    def test_create_defaults(self):
        info = ProviderInfo()
        assert info.name == ""
        assert info.models == []
        assert info.extra_body_map is None

    def test_parse_extracts_catalog(self):
        """0.2-style config: name, models list, per-model extra_body."""
        raw = {
            "current": {"provider": "deepseek", "model": "deepseek-v4-pro"},
            "providers": {
                "deepseek": {
                    "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-test",
                    "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                    "extra_body": {
                        "deepseek-v4-flash": {"thinking": {"type": "enabled"}},
                        "deepseek-v4-pro": {"thinking": {"type": "enabled"}},
                    },
                },
            },
        }
        c = Config.from_dict(raw)
        info = c.provider_info["deepseek"]
        assert info.name == "DeepSeek"
        assert info.models == ["deepseek-v4-flash", "deepseek-v4-pro"]
        assert info.extra_body_map == {
            "deepseek-v4-flash": {"thinking": {"type": "enabled"}},
            "deepseek-v4-pro": {"thinking": {"type": "enabled"}},
        }
        # Active provider+model resolved correctly
        assert c.provider == "deepseek"
        assert c.current.model == "deepseek-v4-pro"
        assert c.current.extra_body == {"thinking": {"type": "enabled"}}

    def test_roundtrip_preserves_catalog(self):
        c = Config(
            provider="deepseek",
            providers={
                "deepseek": ProviderConfig(
                    api_key="sk-test",
                    model="deepseek-v4-pro",
                    base_url="https://api.deepseek.com",
                ),
            },
            provider_info={
                "deepseek": ProviderInfo(
                    name="DeepSeek",
                    models=["deepseek-v4-flash", "deepseek-v4-pro"],
                    extra_body_map={
                        "deepseek-v4-flash": {"thinking": {"type": "enabled"}},
                        "deepseek-v4-pro": {"thinking": {"type": "enabled"}},
                    },
                ),
            },
        )
        d = c.to_dict()
        c2 = Config.from_dict(d)
        info = c2.provider_info["deepseek"]
        assert info.name == "DeepSeek"
        assert info.models == ["deepseek-v4-flash", "deepseek-v4-pro"]
        assert info.extra_body_map == {
            "deepseek-v4-flash": {"thinking": {"type": "enabled"}},
            "deepseek-v4-pro": {"thinking": {"type": "enabled"}},
        }
        # extra_body resolves to the active model's entry
        assert c2.current.extra_body == {"thinking": {"type": "enabled"}}

    def test_flat_extra_body_wrapped_under_active_model(self):
        """When extra_body is a flat dict (not keyed by model), it's wrapped under the active model."""
        raw = {
            "current": {"provider": "p", "model": "m1"},
            "providers": {
                "p": {
                    "api_key": "k",
                    "model": "m1",
                    "models": ["m1"],
                    "extra_body": {"thinking": {"type": "enabled"}},
                },
            },
        }
        c = Config.from_dict(raw)
        info = c.provider_info["p"]
        # Flat dict gets wrapped under the active model for the map
        assert info.extra_body_map == {"m1": {"thinking": {"type": "enabled"}}}
        # And applies to the current provider config
        assert c.current.extra_body == {"thinking": {"type": "enabled"}}

    def test_no_extra_body(self):
        raw = {
            "current": {"provider": "p", "model": "m"},
            "providers": {
                "p": {
                    "name": "P",
                    "api_key": "k",
                    "models": ["m"],
                    "extra_body": None,
                },
            },
        }
        c = Config.from_dict(raw)
        info = c.provider_info["p"]
        assert info.extra_body_map is None
        assert c.current.extra_body is None

    def test_provider_info_add_remove_roundtrip(self):
        """Adding/removing a ProviderInfo entry survives to_dict/from_dict."""
        c = Config(
            provider="a",
            providers={"a": ProviderConfig(api_key="k", model="m1")},
            provider_info={"a": ProviderInfo(name="A", models=["m1"])},
        )
        # Add a new provider + info
        c.providers["b"] = ProviderConfig(api_key="k2", model="m2")
        c.provider_info["b"] = ProviderInfo(name="B", models=["m2", "m3"])

        d = c.to_dict()
        c2 = Config.from_dict(d)
        assert "b" in c2.providers
        assert c2.providers["b"].api_key == "k2"
        assert c2.provider_info["b"].name == "B"
        assert c2.provider_info["b"].models == ["m2", "m3"]

        # Remove it
        del c2.providers["b"]
        del c2.provider_info["b"]
        d2 = c2.to_dict()
        c3 = Config.from_dict(d2)
        assert "b" not in c3.providers
        assert "b" not in c3.provider_info

    def test_prune_models_preserves_extra_body_for_remaining(self):
        """When models list is pruned, extra_body_map entries for remaining models stay."""
        c = Config(
            provider="p",
            providers={"p": ProviderConfig(api_key="k", model="m1")},
            provider_info={
                "p": ProviderInfo(
                    name="P",
                    models=["m1", "m2", "m3"],
                    extra_body_map={
                        "m1": {"a": 1},
                        "m2": {"b": 2},
                        "m3": {"c": 3},
                    },
                ),
            },
        )
        info = c.provider_info["p"]
        # Prune m2 out
        info.models = ["m1", "m3"]
        del info.extra_body_map["m2"]

        d = c.to_dict()
        c2 = Config.from_dict(d)
        info2 = c2.provider_info["p"]
        assert info2.models == ["m1", "m3"]
        assert "m1" in info2.extra_body_map
        assert "m3" in info2.extra_body_map
        assert "m2" not in info2.extra_body_map

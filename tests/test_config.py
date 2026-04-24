"""Config tests — v0.2 (pure data, no persistence)"""

import pytest

from mocode.config import Config, CurrentConfig, ModeConfig, ProviderConfig


class TestConfigLoad:
    def test_default_config(self):
        config = Config()
        assert config.current.provider == "zhipu"
        assert config.current.model == "glm-5"
        assert config.max_tokens == 8192
        assert config.tool_result_limit == 25000
        assert config.current_mode == "normal"

    def test_from_dict(self):
        data = {
            "current": {"provider": "custom", "model": "my-model"},
            "providers": {
                "custom": {
                    "name": "Custom",
                    "base_url": "https://api.custom.com/v1",
                    "api_key": "key123",
                    "models": ["my-model"],
                }
            },
            "max_tokens": 4096,
        }
        config = Config.from_dict(data)
        assert config.current.provider == "custom"
        assert config.current.model == "my-model"
        assert config.max_tokens == 4096

    def test_to_dict(self):
        config = Config.from_dict({
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "max_tokens": 2048,
        })
        d = config.to_dict()
        assert d["current"]["provider"] == "t"
        assert d["max_tokens"] == 2048


class TestConfigMutations:
    def test_set_model(self, config):
        config.set_model("new-model")
        assert config.model == "new-model"

    def test_set_model_adds_to_provider(self, config):
        config.set_model("brand-new-model")
        assert "brand-new-model" in config.models

    def test_set_provider(self, config):
        config.add_provider("other", "Other", "https://other.com/v1", "key")
        config.set_provider("other")
        assert config.current.provider == "other"

    def test_set_provider_unknown_raises(self, config):
        with pytest.raises(ValueError, match="Unknown provider"):
            config.set_provider("nonexistent")

    def test_add_provider(self, config):
        config.add_provider("new", "New Provider", "https://new.com/v1", "key", ["m1"])
        assert "new" in config.providers
        assert config.providers["new"].name == "New Provider"

    def test_add_provider_duplicate(self, config):
        with pytest.raises(ValueError, match="already exists"):
            config.add_provider("test", "Dup", "https://dup.com/v1", "key")

    def test_remove_provider(self, config):
        config.add_provider("extra", "Extra", "https://extra.com/v1", "key", ["m1"])
        config.remove_provider("extra")
        assert "extra" not in config.providers

    def test_remove_last_provider(self, config):
        keys = list(config.providers.keys())
        for k in keys[1:]:
            del config.providers[k]
        with pytest.raises(ValueError, match="last provider"):
            config.remove_provider(keys[0])

    def test_remove_current_provider_switches(self, config):
        config.add_provider("extra", "Extra", "https://extra.com/v1", "key", ["m1"])
        config.set_provider("extra")
        new_current = config.remove_provider("extra")
        assert new_current is not None
        assert config.current.provider != "extra"

    def test_add_model(self, config):
        config.add_model("new-model")
        assert "new-model" in config.models

    def test_remove_model(self, config):
        config.add_model("to-remove")
        config.remove_model("to-remove")
        assert "to-remove" not in config.models

    def test_update_provider(self, config):
        config.update_provider("test", name="Updated Name")
        assert config.providers["test"].name == "Updated Name"


class TestConfigModes:
    def test_mode_switch(self, config):
        assert config.set_mode("yolo")
        assert config.current_mode == "yolo"

    def test_mode_switch_back(self, config):
        config.set_mode("yolo")
        assert config.set_mode("normal")
        assert config.current_mode == "normal"

    def test_mode_switch_invalid(self, config):
        assert not config.set_mode("nonexistent")


class TestConfigProperties:
    def test_api_key(self, config):
        assert config.api_key == "test-key"

    def test_base_url(self, config):
        assert config.base_url == "https://api.test.com/v1"

    def test_models(self, config):
        assert config.models == ["test-model"]

    def test_display_name(self, config):
        assert "test-model" in config.display_name
        assert "Test Provider" in config.display_name

    def test_current_provider(self, config):
        p = config.current_provider
        assert p is not None
        assert p.name == "Test Provider"

    def test_current_provider_missing(self):
        config = Config()
        config.current.provider = "nonexistent"
        assert config.current_provider is None


class TestConfigExtraBody:
    def test_extra_body_none_by_default(self, config):
        assert config.extra_body is None

    def test_extra_body_from_dict(self):
        data = {
            "current": {"provider": "test", "model": "m1"},
            "providers": {
                "test": {
                    "name": "Test",
                    "base_url": "https://test.com/v1",
                    "api_key": "k",
                    "models": ["m1"],
                    "extra_body": {
                        "m1": {"thinking": {"type": "enabled"}},
                    },
                }
            },
        }
        config = Config.from_dict(data)
        assert config.extra_body == {"thinking": {"type": "enabled"}}

    def test_extra_body_roundtrip(self):
        data = {
            "current": {"provider": "test", "model": "m1"},
            "providers": {
                "test": {
                    "name": "Test",
                    "base_url": "https://test.com/v1",
                    "api_key": "k",
                    "models": ["m1"],
                    "extra_body": {"m1": {"thinking": {"type": "enabled"}}},
                }
            },
        }
        config = Config.from_dict(data)
        d = config.to_dict()
        assert d["providers"]["test"]["extra_body"]["m1"] == {"thinking": {"type": "enabled"}}

    def test_extra_body_returns_none_for_missing_model(self):
        data = {
            "current": {"provider": "test", "model": "m2"},
            "providers": {
                "test": {
                    "name": "Test",
                    "base_url": "https://test.com/v1",
                    "api_key": "k",
                    "models": ["m1", "m2"],
                    "extra_body": {"m1": {"thinking": {"type": "enabled"}}},
                }
            },
        }
        config = Config.from_dict(data)
        assert config.extra_body is None

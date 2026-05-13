"""Tests for mocode.app.config — Config, ProviderConfig, load/save."""

import json

import pytest

from mocode.app.config import Config, ProviderConfig


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

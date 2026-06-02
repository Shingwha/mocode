"""Tests for mocode.app.config — Config, ModelEntry, ProviderEntry, load/save."""

import json

import pytest

from mocode.app.config import Config, ModelEntry, ProviderEntry


class TestModelEntry:
    def test_create_minimal(self):
        m = ModelEntry(name="deepseek-chat")
        assert m.name == "deepseek-chat"
        assert m.extra_body is None

    def test_create_with_extra_body(self):
        m = ModelEntry(
            name="deepseek-reasoner",
            extra_body={"thinking": {"type": "enabled"}},
        )
        assert m.extra_body == {"thinking": {"type": "enabled"}}


class TestProviderEntry:
    def test_create_defaults(self):
        entry = ProviderEntry()
        assert entry.name == ""
        assert entry.api_key == ""
        assert entry.base_url is None
        assert entry.models == []

    def test_model_names(self):
        entry = ProviderEntry(
            name="DeepSeek",
            api_key="sk-test",
            models=[
                ModelEntry(name="deepseek-chat"),
                ModelEntry(name="deepseek-reasoner"),
            ],
        )
        assert entry.model_names() == ["deepseek-chat", "deepseek-reasoner"]

    def test_get_extra_body(self):
        entry = ProviderEntry(
            models=[
                ModelEntry(name="m1"),
                ModelEntry(name="m2", extra_body={"thinking": {"type": "enabled"}}),
            ],
        )
        assert entry.get_extra_body("m1") is None
        assert entry.get_extra_body("m2") == {"thinking": {"type": "enabled"}}
        assert entry.get_extra_body("unknown") is None


class TestConfig:
    def test_create(self):
        c = Config(
            active_provider="deepseek",
            active_model="deepseek-chat",
            providers={
                "deepseek": ProviderEntry(
                    api_key="sk-test",
                    models=[ModelEntry(name="deepseek-chat")],
                ),
            },
        )
        assert c.active_provider == "deepseek"
        assert c.active_model == "deepseek-chat"
        assert len(c.providers) == 1
        assert c.max_tokens == 8192

    def test_current_property(self):
        entry = ProviderEntry(api_key="sk-test", models=[ModelEntry(name="gpt-4o")])
        c = Config(active_provider="openai", active_model="gpt-4o", providers={"openai": entry})
        assert c.current is entry

    def test_current_missing_provider(self):
        c = Config(active_provider="missing", active_model="m", providers={})
        assert c.current is None

    def test_model_property(self):
        c = Config(active_provider="x", active_model="my-model", providers={})
        assert c.model == "my-model"

    def test_extra_body_property(self):
        entry = ProviderEntry(
            api_key="sk-test",
            models=[
                ModelEntry(name="m1"),
                ModelEntry(name="m2", extra_body={"thinking": {"type": "enabled"}}),
            ],
        )
        c = Config(active_provider="p", active_model="m2", providers={"p": entry})
        assert c.extra_body == {"thinking": {"type": "enabled"}}

    def test_extra_body_no_active_model(self):
        c = Config(active_provider="missing", active_model="m", providers={})
        assert c.extra_body is None

    def test_api_key_property(self):
        entry = ProviderEntry(api_key="sk-test")
        c = Config(active_provider="p", active_model="m", providers={"p": entry})
        assert c.api_key == "sk-test"

    def test_to_dict_roundtrip(self):
        c = Config(
            active_provider="deepseek",
            active_model="deepseek-reasoner",
            providers={
                "deepseek": ProviderEntry(
                    name="DeepSeek",
                    api_key="sk-test",
                    base_url="https://api.deepseek.com",
                    models=[
                        ModelEntry(name="deepseek-chat"),
                        ModelEntry(name="deepseek-reasoner", extra_body={"thinking": {"type": "enabled"}}),
                    ],
                ),
                "openai": ProviderEntry(
                    name="OpenAI",
                    api_key="sk-openai",
                    models=[ModelEntry(name="gpt-4o")],
                ),
            },
            max_tokens=4096,
            tool_result_limit=10000,
            tool_timeout=120,
        )
        d = c.to_dict()
        c2 = Config.from_dict(d)
        assert c2.active_provider == c.active_provider
        assert c2.active_model == c.active_model
        assert c2.max_tokens == c.max_tokens
        assert c2.tool_result_limit == c.tool_result_limit
        assert c2.tool_timeout == c.tool_timeout
        assert len(c2.providers) == 2
        assert c2.providers["deepseek"].name == "DeepSeek"
        assert c2.providers["deepseek"].api_key == "sk-test"
        assert c2.providers["deepseek"].base_url == "https://api.deepseek.com"
        assert c2.providers["deepseek"].model_names() == ["deepseek-chat", "deepseek-reasoner"]
        assert c2.providers["deepseek"].get_extra_body("deepseek-reasoner") == {"thinking": {"type": "enabled"}}
        assert c2.providers["openai"].api_key == "sk-openai"
        assert c2.providers["openai"].model_names() == ["gpt-4o"]

    def test_copy_independent(self):
        c = Config(
            active_provider="deepseek",
            active_model="deepseek-chat",
            providers={
                "deepseek": ProviderEntry(
                    api_key="sk-test",
                    models=[ModelEntry(name="deepseek-chat")],
                ),
            },
        )
        c2 = c.copy()
        c2.active_provider = "openai"
        c2.providers["deepseek"].api_key = "changed"
        assert c.active_provider == "deepseek"
        assert c.providers["deepseek"].api_key == "sk-test"

    def test_from_dict_defaults(self):
        c = Config.from_dict({"active_provider": "x", "active_model": "m", "providers": {}})
        assert c.max_tokens == 8192
        assert c.tool_result_limit == 25000
        assert c.tool_timeout == 240

    def test_to_dict_json_format(self):
        """Verify the exact JSON format matches the plan spec."""
        c = Config(
            active_provider="deepseek",
            active_model="deepseek-chat",
            providers={
                "deepseek": ProviderEntry(
                    name="DeepSeek",
                    api_key="sk-...",
                    base_url="https://api.deepseek.com",
                    models=[
                        ModelEntry(name="deepseek-chat"),
                        ModelEntry(name="deepseek-reasoner", extra_body={"thinking": {"type": "enabled"}}),
                    ],
                ),
            },
        )
        d = c.to_dict()
        # Top-level keys
        assert d["active_provider"] == "deepseek"
        assert d["active_model"] == "deepseek-chat"
        assert "providers" in d
        assert "max_tokens" in d
        assert "tool_result_limit" in d
        assert "tool_timeout" in d
        # Provider entry format
        pd = d["providers"]["deepseek"]
        assert pd["name"] == "DeepSeek"
        assert pd["api_key"] == "sk-..."
        assert pd["base_url"] == "https://api.deepseek.com"
        assert len(pd["models"]) == 2
        assert pd["models"][0] == {"name": "deepseek-chat"}
        assert pd["models"][1] == {"name": "deepseek-reasoner", "extra_body": {"thinking": {"type": "enabled"}}}


class TestConfigPersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "config.json"
        c = Config(
            active_provider="deepseek",
            active_model="deepseek-chat",
            providers={
                "deepseek": ProviderEntry(
                    api_key="sk-test",
                    models=[ModelEntry(name="deepseek-chat")],
                ),
            },
        )
        c.save(path)
        assert path.exists()

        c2 = Config.load(path)
        assert c2 is not None
        assert c2.active_provider == "deepseek"
        assert c2.active_model == "deepseek-chat"
        assert c2.providers["deepseek"].api_key == "sk-test"

    def test_load_nonexistent(self, tmp_path):
        path = tmp_path / "nope.json"
        assert Config.load(path) is None

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert Config.load(path) is None

    def test_from_dict_string_models(self):
        """Models can be plain strings in the JSON."""
        raw = {
            "active_provider": "p",
            "active_model": "m1",
            "providers": {
                "p": {
                    "api_key": "k",
                    "models": ["m1", "m2"],
                },
            },
        }
        c = Config.from_dict(raw)
        assert c.providers["p"].model_names() == ["m1", "m2"]

    def test_provider_add_remove_roundtrip(self):
        """Adding/removing a provider survives to_dict/from_dict."""
        c = Config(
            active_provider="a",
            active_model="m1",
            providers={
                "a": ProviderEntry(
                    api_key="k",
                    models=[ModelEntry(name="m1")],
                ),
            },
        )
        c.providers["b"] = ProviderEntry(
            name="B",
            api_key="k2",
            models=[ModelEntry(name="m2"), ModelEntry(name="m3")],
        )

        d = c.to_dict()
        c2 = Config.from_dict(d)
        assert "b" in c2.providers
        assert c2.providers["b"].api_key == "k2"
        assert c2.providers["b"].name == "B"
        assert c2.providers["b"].model_names() == ["m2", "m3"]

        # Remove it
        del c2.providers["b"]
        d2 = c2.to_dict()
        c3 = Config.from_dict(d2)
        assert "b" not in c3.providers

"""Tests for mocode.app.config — Config, ModelEntry, ProviderEntry, load/save."""

from mocode.app.config import Config, ModelEntry, ProviderEntry


class TestModelEntry:
    def test_create_minimal(self):
        m = ModelEntry(name="deepseek-chat")
        assert m.name == "deepseek-chat"
        assert m.extra_body is None


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
        c = Config(
            active_provider="openai", active_model="gpt-4o", providers={"openai": entry}
        )
        assert c.current is entry

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
                        ModelEntry(
                            name="deepseek-reasoner",
                            extra_body={"thinking": {"type": "enabled"}},
                        ),
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
        assert c2.providers["deepseek"].model_names() == [
            "deepseek-chat",
            "deepseek-reasoner",
        ]
        assert c2.providers["deepseek"].get_extra_body("deepseek-reasoner") == {
            "thinking": {"type": "enabled"}
        }
        assert c2.providers["openai"].api_key == "sk-openai"
        assert c2.providers["openai"].model_names() == ["gpt-4o"]

    def test_from_dict_defaults(self):
        c = Config.from_dict(
            {"active_provider": "x", "active_model": "m", "providers": {}}
        )
        assert c.max_tokens == 8192
        assert c.tool_result_limit == 25000
        assert c.tool_timeout == 240


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

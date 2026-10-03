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
    def test_conventional_name(self):
        """同一条命名规则的各种形状：红了看断言消息即知是哪种 key。"""
        cases = {
            "intern": "INTERN_API_KEY",
            "my-gateway": "MY_GATEWAY_API_KEY",
            "OpenAI": "OPENAI_API_KEY",
            "a.b": "A_B_API_KEY",
        }
        for provider_key, expected in cases.items():
            assert env_var_for(provider_key) == expected, provider_key


class TestModelEntry:
    """逐模型条目：从 JSON 实际持有的内容里读出来。

    唯一入口是 :meth:`ModelEntry.from_dict`，它容忍手改过的文件——每个字段
    要么被强转、要么被丢掉，而不是被信任：一个类型不对的字段退化成"未声明"，
    加载不会因此失败。下面的矩阵就是那张容忍表，红了看参数 id 就知道是哪条
    规则出了问题；参数行把同一种规则的若干形状合成一组。
    """

    @pytest.mark.parametrize(
        "raws, expecteds",
        [
            pytest.param(
                [{}, None],
                [ModelEntry(), ModelEntry()],
                id="absent-or-null-means-all-unset",
            ),
            pytest.param(
                [{"id": 42, "name": None}],
                [ModelEntry(id="42")],
                id="id-and-name-take-strings-only",
            ),
            pytest.param(
                [
                    {"context_window": "128000", "max_tokens": "8192"},
                    {"context_window": "huge", "max_tokens": True},
                ],
                [
                    ModelEntry(context_window=128_000, max_tokens=8_192),
                    ModelEntry(),
                ],
                id="limit-fields-coerce-numbers-and-drop-garbage",
            ),
            pytest.param(
                [{"efforts": ["high", "xhigh", "max"]}],
                [ModelEntry(efforts=("high", "xhigh", "max"))],
                id="efforts-take-a-list-of-strings",
            ),
            pytest.param(
                [{"efforts": ["high", 1, None, "max"]}],
                [ModelEntry(efforts=("high", "max"))],
                id="efforts-drop-non-string-items",
            ),
            pytest.param(
                [
                    {"efforts": []},
                    {"efforts": [1, None]},
                    {"efforts": "high"},
                    {"efforts": {"low": 1}},
                    {"efforts": 3},
                ],
                [ModelEntry()] * 5,
                id="efforts-of-the-wrong-shape-are-undeclared",
            ),
            pytest.param(
                [{"effort": "low"}, {"effort": 1}, {"effort": True}, {"effort": ["low"]}],
                [ModelEntry(effort="low"), ModelEntry(), ModelEntry(), ModelEntry()],
                id="effort-takes-a-string-and-nothing-else",
            ),
            pytest.param(
                [{"retry": {"max_attempts": 3, "base_delay": 5.0, "bogus": 1}}],
                [ModelEntry(retry={"max_attempts": 3, "base_delay": 5.0})],
                id="retry-keeps-known-keys-and-drops-unknown-ones",
            ),
            pytest.param(
                [{"retry": {}}, {"retry": "fast"}],
                [ModelEntry(), ModelEntry()],
                id="retry-of-the-wrong-shape-is-unset",
            ),
        ],
    )
    def test_from_dict_coerces_or_drops(self, raws, expecteds):
        for raw, expected in zip(raws, expecteds):
            assert ModelEntry.from_dict(raw) == expected

    def test_roundtrip_and_omission(self):
        """一个声明齐全的条目原样往返；什么都没声明的条目只序列化 id。

        元组序列化成它当初被读到的那个 JSON 数组；声明过的 retry 段现搭成
        策略，没声明的一个都不发明。
        """
        model = ModelEntry(
            id="big",
            name="Big",
            context_window=200_000,
            max_tokens=32_768,
            efforts=("low", "high", "max"),
            effort="high",
            retry={"max_attempts": 3, "base_delay": 5.0},
        )
        assert ModelEntry.from_dict(model.to_dict()) == model
        assert model.to_dict()["efforts"] == ["low", "high", "max"]

        assert ModelEntry().to_dict() == {"id": ""}

        assert model.retry_policy() == RetryPolicy(max_attempts=3, base_delay=5.0)
        assert ModelEntry().retry_policy() is None


class TestProviderEntry:
    def test_defaults_and_label(self):
        entry = ProviderEntry()
        assert entry.name == ""
        assert entry.base_url is None
        assert entry.models == []
        assert entry.model_ids() == []
        assert entry.label("deepseek") == "deepseek"
        assert ProviderEntry(name="DeepSeek").label("deepseek") == "DeepSeek"

    def test_the_models_array_is_parsed_and_skips_what_is_not_an_entry(self):
        """models 是有序数组；没有 id 的、不是对象的条目整条跳过，
        字段本身不是数组则一篇没有。"""
        entry = ProviderEntry.from_dict(
            {"models": [{"id": "a"}, {"id": "b", "max_tokens": 100}]}
        )
        assert entry.model_ids() == ["a", "b"]
        assert entry.model("b").max_tokens == 100

        no_id = ProviderEntry.from_dict(
            {"models": [{"id": "a"}, {"name": "no id"}, {}]}
        )
        assert no_id.model_ids() == ["a"]
        not_dicts = ProviderEntry.from_dict({"models": [{"id": "a"}, "b", None, 3]})
        assert not_dicts.model_ids() == ["a"]
        not_a_list = ProviderEntry.from_dict({"models": {"a": {}}})
        assert not_a_list.models == []

        # 按 id 取条目：命中带出它自己，查无此人是 None
        assert entry.model("a").id == "a"
        assert entry.model("who-knows") is None

    def test_the_key_resolves_explicit_then_environment_then_empty(self, monkeypatch):
        monkeypatch.setenv("DEMO_API_KEY", "from-env")
        assert ProviderEntry(api_key="explicit").api_key_for("demo") == "explicit"
        assert ProviderEntry().api_key_for("demo") == "from-env"

        monkeypatch.delenv("DEMO_API_KEY", raising=False)
        assert ProviderEntry().api_key_for("demo") == ""

    def test_roundtrip_and_omission(self):
        entry = ProviderEntry(
            name="Demo",
            api_key="sk-1",
            base_url="https://example.test/v1",
            models=[ModelEntry(id="m", max_tokens=512)],
        )
        assert ProviderEntry.from_dict(entry.to_dict()) == entry

        bare = ProviderEntry(models=[ModelEntry(id="m")])
        assert bare.to_dict() == {"models": [{"id": "m"}]}


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

    def test_model_spec_resolves_the_declared_entry(self):
        """声明的成对出现；没声明的一个限制都不发明。

        表里每个 (provider, model) 走一遍 ``model_spec``：命中的带出声明值，
        没命中的（模型未知、整个 provider 未知）只有名字、没有限制。
        """
        cases = [
            ("demo", "big", "big", 200_000, 32_768),
            ("demo", "bare", "bare", None, None),
            ("demo", "who-knows", "who-knows", None, None),
            ("ghost", "big", "big", None, None),
        ]
        for provider, model, name, context_window, max_tokens in cases:
            spec = self._config().model_spec(provider, model)
            assert (spec.name, spec.context_window, spec.max_tokens) == (
                name,
                context_window,
                max_tokens,
            ), f"{provider}/{model}"

        # 自定义 efforts 原样穿过；没声明 effort 的条目不发明一个
        custom = self._config().model_spec("demo", "custom")
        assert custom.efforts == ("high", "xhigh", "max")
        assert custom.effort == "xhigh"
        assert self._config().model_spec("demo", "big").effort is None

        # 没声明 efforts 的条目——连同整家未知的 provider——退回内核默认
        assert self._config().model_spec("demo", "big").efforts == EFFORTS
        assert self._config().model_spec("demo", "who-knows").efforts == EFFORTS
        assert self._config().model_spec("ghost", "big").efforts == EFFORTS


class TestConfigSerialization:
    def test_the_agent_block_is_the_core_policy_type(self):
        """One policy type: what the loop runs under is what the file stores —
        读进来是它，写出去也是它，每个字段都过得去；手改过的段被容忍：
        没见过的子键忽略、类型不对的值退回默认、整个段不是对象也只是一段
        空策略。"""
        # 什么都没声明：一份空策略，默认值逐个在场
        empty = Config.from_dict({})
        assert empty.agent == AgentConfig()
        assert empty.agent.tool_timeout == 240
        assert empty.agent.max_iterations == 0
        assert empty.agent.max_tool_calls == 0
        assert empty.agent.max_turn_seconds == 0
        assert empty.agent.tool_result_limit == 50000

        # 声明过的字段读进来就是它自己
        parsed = Config.from_dict(
            {"agent": {"tool_timeout": 30, "max_turn_seconds": 120, "tool_result_limit": 9000}}
        )
        assert parsed.agent == AgentConfig(
            tool_timeout=30, max_turn_seconds=120, tool_result_limit=9000
        )

        # 写出去再读回来，一个字段都不少
        original = Config(agent=AgentConfig(tool_timeout=45, max_iterations=7, max_tool_calls=99))
        assert Config.from_dict(original.to_dict()).agent == original.agent

        fallback = AgentConfig()

        # 没见过的子键被忽略，认得的那个照常生效
        unknown = Config.from_dict({"agent": {"tool_timeout": 30, "mystery": True}})
        assert unknown.agent.tool_timeout == 30
        assert not hasattr(unknown.agent, "mystery")

        # 类型不对的值退回默认，加载不因此失败
        garbage = Config.from_dict({"agent": {"tool_timeout": "soon", "max_iterations": None}})
        assert garbage.agent.tool_timeout == fallback.tool_timeout
        assert garbage.agent.max_iterations == fallback.max_iterations

        # 整个段不是对象：一段空策略，别的键照常读
        not_a_section = Config.from_dict({"agent": None, "model": "m"})
        assert not_a_section.agent == AgentConfig()
        assert not_a_section.model == "m"

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

        # 顶层那一对：声明了就照原样写出来（键在顶层，不是嵌套结构）
        assert original.to_dict()["provider"] == "demo"
        assert original.to_dict()["model"] == "m"

        # 没声明的留着空串——不是缺键；空的 plugins 映射同样是自有键
        unset = Config(providers={"demo": ProviderEntry()})
        data = unset.to_dict()
        assert data["provider"] == ""
        assert data["model"] == ""
        assert data["providers"] == {"demo": {"models": []}}
        assert "plugins" in data  # an empty mapping is still owned, not omitted

        # 一个文件里根本不存在的 provider：读得进来，current 就是没有
        assert Config.from_dict({"provider": "ghost", "providers": {}}).current is None

    def test_foreign_keys_survive_and_owned_keys_win(self):
        """Keys MoCode does not own must not be dropped when it saves — and a
        stale foreign copy never overrides a real owned key."""
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

        owned = Config.from_dict({"model": "real", "providers": {}})
        owned.foreign["model"] = "stale"
        assert owned.to_dict()["model"] == "real"


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

        # 记住自己从哪来：不带参数的 save() 写回原处
        custom = tmp_path / "custom.json"
        Config(model="m").save(custom)
        back = Config.load(custom)
        assert back.path == custom
        back.save()
        assert json.loads(custom.read_text(encoding="utf-8"))["model"] == "m"

        # 读不出来而不是崩：文件不存在、或者根本不是 JSON
        assert Config.load(tmp_path / "nope.json") is None

        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert Config.load(broken) is None

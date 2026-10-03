"""The runtime, and the terminal that is one runtime plus a REPL."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mocode.core.events import TextDelta
from mocode.core.provider import Response, Usage
from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.runtime import MoCode
from mocode.testing import collect, events_of_type, terminal

from .conftest import make_config, make_mc, strip_ansi, wire, write_plugin

class TestThePackage:
    def test_importing_mocode_stays_lazy(self):
        """``import mocode`` must not drag the host layer in with it.

        PEP 562 is the contract (AGENTS.md: under a millisecond); a subprocess
        checks it on a pristine interpreter, because the first access caches
        the name and in-process order would be lies.
        """
        import subprocess
        import sys

        code = (
            "import mocode\n"
            "assert 'MoCode' not in vars(mocode), 'eagerly imported'\n"
            "assert mocode.MoCode is not None  # …yet resolves on demand\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert done.returncode == 0, done.stderr


class TestTheRuntime:
    def test_a_missing_config_is_reported_clearly(self, tmp_path: Path):
        with patch("mocode.host.runtime.Config.load", return_value=None):
            with pytest.raises(ValueError, match="config.json"):
                MoCode(home=tmp_path / "home", plugin_dirs=[])  # the missing-config path

    def test_everything_it_owns_lives_under_home(self, make_mc, tmp_path: Path):
        """Nothing the runtime writes escapes its home — not even sessions —
        而打开一个会话本身什么都不写。"""
        mc = make_mc()
        mc.config.save = lambda *a, **k: None

        assert mc.home == tmp_path / "home"

        # 打开会话不是写盘：id 在手，store 还空着
        opening = mc.new_conversation(cwd=tmp_path)
        assert opening.id.startswith("session_")
        assert mc.store.list_all() == []

        # A conversation whose project lies outside the home still records
        # its session inside it — and the store reads it back from there.
        conversation = mc.new_conversation(cwd=tmp_path)
        conversation.messages.append({"role": "user", "content": "hi"})
        saved = conversation.save()

        assert saved is not None
        assert (tmp_path / "home" / "sessions").is_dir()
        assert [s.id for s in mc.store.list_all()] == [saved.id]

        resumed = mc.resume(saved.id)
        assert resumed is not None
        assert resumed.messages == conversation.messages

    def test_it_holds_no_conversation(self, mc: MoCode):
        """Opening is the runtime's job; being one is not."""
        assert not hasattr(mc, "chat")
        assert not hasattr(mc, "messages")
        assert not hasattr(mc, "agent")

    def test_the_default_provider_type_is_openai(self, mc: MoCode):
        from mocode.providers.openai import OpenAIProvider

        provider = mc.provider_for("test", "test-model")
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "test-model"

    def test_a_per_model_retry_override_reaches_the_provider(self, mc: MoCode):
        """The model entry's `retry` block beats the provider's own policy —
        and a model without one keeps it."""
        from mocode.core.provider import RetryPolicy

        mc.config.providers["test"].model("test-model").retry = {
            "max_attempts": 3,
            "base_delay": 5.0,
        }

        overridden = mc.provider_for("test", "test-model")
        assert overridden.retry_policy == RetryPolicy(max_attempts=3, base_delay=5.0)

        plain = mc.provider_for("test", "other-model")
        assert plain.retry_policy == RetryPolicy(honor_retry_after=True)


class TestProviderTypes:
    """A provider implementation is a capability, and arrives from outside."""

    @staticmethod
    def _fake_factory(entry, key, model):
        return SimpleNamespace(model=model, built_from=key, api_key=entry.api_key_for(key))

    def test_a_registered_type_is_built_and_an_unregistered_one_is_a_mistake(
        self, make_mc
    ):
        config = make_config()
        config.providers["local"] = ProviderEntry(
            type="local", models=[ModelEntry(id="llama")]
        )
        mc = make_mc(config)
        mc.register_provider_type("local", self._fake_factory)

        provider = mc.provider_for("local", "llama")

        assert (provider.model, provider.built_from) == ("llama", "local")
        assert provider.api_key == ""  # LOCAL_API_KEY is not set

        # 没注册过的 type 是配置错误：报错点名缺的是哪次注册
        bare = make_mc(config)
        with pytest.raises(ValueError, match="register_provider_type"):
            bare.provider_for("local", "llama")

    def test_the_type_round_trips_through_the_file(self, tmp_path: Path):
        config = make_config()
        config.providers["local"] = ProviderEntry(
            type="local", models=[ModelEntry(id="llama")]
        )
        path = tmp_path / "config.json"
        config.save(path)

        loaded = Config.load(path)

        assert loaded.providers["local"].type == "local"
        # The default stays implicit: a file without "type" means openai.
        assert loaded.providers["test"].type == "openai"
        assert "type" not in config.providers["test"].to_dict()


PLUGGED_CODE = """
from mocode.plugins import Plugin


class PluggedProvider:
    \"\"\"A structural Provider — never streamed in these tests.\"\"\"

    def __init__(self, model):
        self._model = model

    @property
    def model(self):
        return self._model

    def is_retriable(self, exc):
        return False

    async def stream(self, messages, system, tools, max_tokens):
        yield  # pragma: no cover


def factory(entry, key, model):
    return PluggedProvider(model)


class PluggedPlugin(Plugin):
    name = "plugged"
    description = "ships a provider type"

    def build(self, ctx):
        ctx.register_provider_type("plugged", factory)
"""


class TestPluginProviderTypes:
    """A plugin may ship the provider implementation its own conversation runs on."""

    @staticmethod
    def _plugged_runtime(make_mc, tmp_path: Path) -> MoCode:
        plugins_dir = tmp_path / "plugins"
        write_plugin(plugins_dir, "plugged", PLUGGED_CODE)

        config = make_config()
        config.providers["local"] = ProviderEntry(
            type="plugged", models=[ModelEntry(id="llama")]
        )
        return make_mc(config, plugin_dirs=[plugins_dir])

    def test_the_registering_conversation_runs_on_it_and_a_later_registration_replaces_it(
        self, make_mc, tmp_path: Path
    ):
        """build() runs before the provider is resolved, so the conversation
        that shipped the type uses it — no second conversation needed; and the
        runtime's own registry keeps its rule: last registration wins."""
        mc = self._plugged_runtime(make_mc, tmp_path)

        conversation = mc.new_conversation(
            cwd=tmp_path, provider="local", model="llama"
        )

        from mocode_plugin_plugged import PluggedProvider

        assert isinstance(conversation.agent.provider, PluggedProvider)
        assert conversation.agent.provider.model == "llama"

        mc.register_provider_type(
            "plugged", lambda entry, key, model: SimpleNamespace(model=model)
        )

        assert isinstance(mc.provider_for("local", "llama"), SimpleNamespace)


class TestAConversation:
    async def test_streams_events_and_answers(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        wire(
            conversation,
            Response(content="hi there", usage=Usage(2, 3), finish_reason="stop"),
            chunk_size=2,
        )

        events = await collect(conversation.stream("hello"))

        texts = [e.text for e in events_of_type(events, TextDelta)]
        assert "".join(texts) == "hi there"
        assert terminal(events).content == "hi there"
        assert conversation.state.answer == "hi there"
        assert conversation.messages[0] == {"role": "user", "content": "hello"}

        # 全程没有一个 display 对象：agent 就在 host 的 ctx 上
        assert conversation.host.ctx.agent is conversation.agent


class TestTheTerminal:
    """`CLIApp` is a runtime, one conversation, and a terminal in front of it."""

    @pytest.fixture
    def app(self, tmp_path: Path):
        from mocode.cli import CLIApp

        return CLIApp(
            config=make_config(), home=tmp_path / "home",
            interactive=True, plugin_dirs=[],
        )

    def test_its_plugin_contributes_the_commands_and_the_host_knows_nothing_of_either(
        self, app, tmp_path: Path
    ):
        """终端自己的命令来自它自己的插件；renderer 是 app 自己装的——host
        的插件集（这里是内建的）两样都没有。命令属于 host：headless 也照旧有。
        """
        assert "/help" in {c.name for c in app.commands.all()}
        assert "cli" in [p.name for p in app.plugins]

        from mocode.cli import CLIApp
        from mocode.cli.render import CLIRenderer

        assert isinstance(app.renderer, CLIRenderer)
        # The renderer is the app's own doing — the host's plugin set (the
        # built-ins, here) contains nothing that installed it.
        assert "cli" not in [p.name for p in app.runtime.plugins_for(tmp_path)]

        headless = CLIApp(config=make_config(), home=tmp_path / "home", interactive=False)
        assert "/help" in {c.name for c in headless.commands.all()}

    def test_it_writes_sessions_under_its_own_home(self, app, tmp_path: Path):
        app.conversation.messages.append({"role": "user", "content": "hi"})
        app.conversation.save()

        assert (tmp_path / "home" / "sessions").is_dir()
        assert app.runtime.store.list_all() != []

    async def test_a_turn_is_echoed_and_answered(self, app, capsys):
        """The interactive path end to end — the one a user actually walks into.

        Everything here is reached only by running the REPL, which is exactly
        why a renamed display primitive can otherwise break the app silently.
        """
        wire(
            app.conversation,
            Response(content="pong", usage=Usage(1, 1), finish_reason="stop"),
        )
        typed = iter(["ping", "/quit"])

        async def scripted_prompt() -> str:
            return next(typed)

        app.display.prompt = scripted_prompt
        await app._repl()

        out = strip_ansi(capsys.readouterr().out.replace("\r", "\n"))
        rendered = [line.rstrip() for line in out.splitlines() if line.strip()]
        assert rendered[:2] == ["❯ ping", "pong"]
        assert rendered[2] == "↑1 ↓1 tokens"   # what the turn cost
        assert set(rendered[3]) == {"─"}       # the rule closes it before the next prompt

    async def test_a_command_speaks_through_the_same_stream(self, app, capsys):
        """`/help` publishes a notice; the renderer draws it like anything else."""
        app.display.clear_screen = lambda: None
        typed = iter(["/help", "/quit"])

        async def scripted_prompt() -> str:
            return next(typed)

        app.display.prompt = scripted_prompt
        await app._repl()

        out = strip_ansi(capsys.readouterr().out)
        # 列出的就是命令注册表本身：从文本提取命令名与注册表对账——一个不少，
        # 也一个不多；命令描述话术不在契约内。
        listed = {
            match.group(1)
            for line in out.splitlines()
            if (match := re.match(r"^  (\S+)", line))
        }
        assert listed == {c.name for c in app.commands.all()}

    def test_the_display_attaches_only_when_a_frontend_is_asked_for(self, tmp_path: Path):
        """display/renderer 的挂载规则：非交互默认什么都不挂（那正是 -p
        保持为管道的原因）；render 只能要一个前端；interactive 必然带一个
        ——render 可以要，永远收不走。"""
        from mocode.cli import CLIApp

        def _app(**flags):
            return CLIApp(
                config=make_config(), home=tmp_path / "home", **flags
            )

        rendered = _app(interactive=False, render=True)
        assert rendered.display is not None and rendered.renderer is not None

        interactive = _app(interactive=True, render=False)
        assert interactive.display is not None

        piped = _app(interactive=False)
        assert piped.display is None
        assert piped.renderer is None

    def test_a_one_shot_prints_the_answer_once_and_records_nothing(
        self, tmp_path: Path, capsys
    ):
        """一次性运行不是会话：答案打一遍（渲染过的 run 不会打两遍），
        什么也不存档。"""
        from mocode.cli import CLIApp

        app = CLIApp(config=make_config(), home=tmp_path / "home", interactive=False)
        wire(
            app.conversation,
            Response(content="answer", usage=Usage(1, 1), finish_reason="stop"),
        )

        app.run_oneshot("hello")

        assert capsys.readouterr().out.strip() == "answer"
        assert app.runtime.store.list_all() == []

        drawn = CLIApp(
            config=make_config(), home=tmp_path / "home",
            interactive=False, render=True,
        )
        drawn.display.clear_screen = lambda: None
        wire(
            drawn.conversation,
            Response(content="drawn", usage=Usage(1, 1), finish_reason="stop"),
        )

        drawn.run_oneshot("hello")

        assert strip_ansi(capsys.readouterr().out).count("drawn") == 1

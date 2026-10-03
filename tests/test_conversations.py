"""Conversations — many at once, in different projects, on different models.

This is what the runtime exists for: one process holding N conversations that
share the config, the plugin loading and the session store, and share nothing
else. The tests here are the ones that would have failed before the runtime
grew a conversation object: two turns running at once, two projects with their
own working directory, two histories, two models.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from mocode.core.agent import AgentConfig
from mocode.core.events import Notice, PluginMessage
from mocode.core.provider import ModelSpec
from mocode.host.events import ConversationChanged
from mocode.host.runtime import MoCode
from mocode.host.session import Session
from mocode.testing import MockProvider, SlowProvider, collect, say, terminal, tool_call_response

from .conftest import make_config, make_mc, project, wire, wired, write_plugin


def _drain(subscription) -> list:
    """Everything a subscription has buffered, oldest first."""
    events = []
    while (event := subscription.take()) is not None:
        events.append(event)
    return events


# ── the conversation as a unit ──────────────────────────────


class TestAConversationIsItsOwn:
    def test_two_conversations_share_nothing(self, mc: MoCode, tmp_path: Path):
        """会话隔离：cwd、id、工具注册表、命令注册表两两不相交，
        一个会话写进的历史另一个读不到。"""
        first = mc.new_conversation(cwd=project(tmp_path, "a"))
        second = mc.new_conversation(cwd=project(tmp_path, "b"))

        assert first.cwd != second.cwd
        assert first.id != second.id
        assert first.id.startswith("session_")
        assert first.tools.get("bash") is not second.tools.get("bash")
        assert first.commands is not second.commands

        first.messages.append({"role": "user", "content": "hello from a"})

        assert second.messages == []

    async def test_the_system_prompt_names_the_project(self, mc: MoCode, tmp_path: Path):
        workdir = project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=workdir)
        await conversation.prepare()

        assert f"cwd: {workdir}" in conversation.agent.system_prompt


class TestToolsWorkInTheConversationsProject:
    async def test_bash_starts_in_the_project_and_shares_no_state(
        self, mc: MoCode, tmp_path: Path
    ):
        """A shell starts in the project, and what one conversation did in
        its shell is invisible to the next: a ``cd`` and an exported variable
        never crossed. Distinct words: a shell reports its directory in its own
        notation (MSYS form under Git Bash on Windows), so compare by name."""
        workdir = project(tmp_path, "alpha")
        elsewhere = project(tmp_path, "beta")
        first = mc.new_conversation(cwd=workdir)
        second = mc.new_conversation(cwd=workdir)
        bash = first.tools.get("bash")

        assert workdir.name in (await bash.run_async({"command": "pwd"}, None)).content

        await bash.run_async({"command": f"cd {elsewhere}"}, None)
        await bash.run_async({"command": "export LEAK=1"}, None)

        there = await second.tools.get("bash").run_async({"command": "pwd"}, None)
        env = await second.tools.get("bash").run_async({"command": "echo $LEAK"}, None)

        assert "alpha" in there.content
        assert "beta" not in there.content
        assert env.content == "(empty)"

    def test_relative_paths_resolve_into_the_project(self, mc: MoCode, tmp_path: Path):
        workdir = project(tmp_path, "a")
        (workdir / "notes.txt").write_text("hello", encoding="utf-8")
        conversation = mc.new_conversation(cwd=workdir)

        result = conversation.tools.get("read").run({"path": "notes.txt"})

        assert "hello" in result.content

    def test_skills_come_from_the_project(self, mc: MoCode, tmp_path: Path):
        workdir = project(tmp_path, "a")
        skill = workdir / ".mocode" / "skills" / "deploy"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: ship it\n---\n\nRun the deploy.",
            encoding="utf-8",
        )

        conversation = mc.new_conversation(cwd=workdir)

        assert "/skill:deploy" in {c.name for c in conversation.commands.all()}


# ── concurrency ─────────────────────────────────────────────


class TestConcurrency:
    async def test_two_conversations_run_at_the_same_time(self, wired, tmp_path: Path):
        """Two turns genuinely overlap: the first conversation's tool holds
        its call open until the second conversation's request actually goes
        out — an observed event, not a duration the overlap is hoped to fit
        inside."""
        other_started = asyncio.Event()

        class SecondStarted(MockProvider):
            async def stream(self, *args):
                other_started.set()
                async for chunk in super().stream(*args):
                    yield chunk

        async def slow(args, ctx):
            await other_started.wait()
            return "slept"

        from mocode.core.tool import Tool

        first, _ = wired(tool_call_response("wait"), "first done", cwd=project(tmp_path, "a"))
        second, _ = wired("second done", cwd=project(tmp_path, "b"))
        first.tools.register(Tool("wait", "d", {}, slow, with_context=True))
        second.agent.provider = SecondStarted([say("second done")])

        first_turn = first.run("hello from a")
        second_turn = second.run("hello from b")
        first_result, second_result = await asyncio.gather(
            first_turn.wait(), second_turn.wait()
        )

        assert first_result.content == "first done"
        assert second_result.content == "second done"
        # A tool registered before the first turn is part of the surface the
        # interface freezes from — not news. (Late registration — after the
        # surface materialized — is cache-protect's own test.)
        assert first.messages[0]["content"] == "hello from a"
        assert [m["content"] for m in second.messages][0] == "hello from b"

    async def test_a_busy_conversation_refuses_more_work(
        self, mc: MoCode, tmp_path: Path
    ):
        """一个会话同时只跑一个 turn：第二个 run() 直接抛错，而不是把两段
        历史交错起来；new_session() 会在运行的 turn 底下换历史，同样拒绝。"""
        conversation = mc.new_conversation(cwd=project(tmp_path, "a"))
        conversation.agent.provider = SlowProvider()

        turn = conversation.run("hi")
        assert conversation.busy
        with pytest.raises(RuntimeError, match="already running"):
            conversation.run("again")
        with pytest.raises(RuntimeError, match="cancel it first"):
            await conversation.new_session()

        conversation.cancel()
        assert (await turn.wait()).cancelled

    async def test_stopping_one_conversation_leaves_the_other_alone(
        self, mc: MoCode, tmp_path: Path, wired
    ):
        entered = asyncio.Event()

        class Stopped(MockProvider):
            async def stream(self, *args):
                entered.set()  # the request is in flight — safe to cancel
                # A gate that never opens: the turn parks here until it is
                # cancelled, which is the thing under test. An event, not a
                # sleep — nothing about the overlap depends on a duration.
                await asyncio.Event().wait()
                yield  # pragma: no cover - never reached

        stopped = mc.new_conversation(cwd=project(tmp_path, "a"))
        stopped.agent.provider = Stopped()
        running, _ = wired("still here", cwd=project(tmp_path, "b"))

        turn = stopped.run("hi")
        await asyncio.wait_for(entered.wait(), 5)
        stopped.cancel()

        assert (await turn.wait()).cancelled
        assert await running.chat("hello") == "still here"

    async def test_a_reader_sees_turns_and_notices(self, wired, tmp_path: Path):
        """观察通道：一个没有启动该 turn 的订阅者也看得到整条事件流；
        两回合之间的公告同样到达读者。"""
        conversation, _ = wired("watched", cwd=project(tmp_path, "a"))
        reader = conversation.subscribe()

        await conversation.chat("hi")

        assert terminal(_drain(reader)).content == "watched"

        await conversation.notify("something happened", level="warn")

        event = reader.take()
        assert isinstance(event, Notice)
        assert (event.message, event.level) == ("something happened", "warn")


# ── the model is per conversation ───────────────────────────


class TestModel:
    def test_set_model_changes_only_this_conversation(self, mc: MoCode, tmp_path: Path):
        """模型是每会话的：set_model 切的是这一个会话，构造时的 provider/
        model 覆盖也只是这一个会话的起点。"""
        first = mc.new_conversation(cwd=project(tmp_path, "a"))
        second = mc.new_conversation(cwd=project(tmp_path, "b"))

        first.set_model("second", "second-model")

        assert (first.provider_key, first.model_name) == ("second", "second-model")
        assert (second.provider_key, second.model_name) == ("test", "test-model")
        assert first.agent.provider is not second.agent.provider

        override = mc.new_conversation(
            cwd=project(tmp_path, "a"), provider="second", model="second-model"
        )
        assert override.model_name == "second-model"
        assert override.model == mc.config.model_spec("second", "second-model")

        # 没注册过的 provider 是配置错误：报错点名缺的是哪一次
        with pytest.raises(ValueError, match="not defined"):
            first.set_model("nope", "m")

    def test_per_conversation_model_changes_never_write_the_config(
        self, mc: MoCode, tmp_path: Path
    ):
        """换模型、换 effort 都是这个会话自己的决定：config.json 一个字节
        都不动（唯一写它的是 set_default_model）。"""
        conversation = mc.new_conversation(cwd=project(tmp_path, "a"))
        mc.config.save = lambda *a, **k: pytest.fail("switching a model is not a config write")

        conversation.set_model("second", "second-model")
        conversation.set_effort("max")

        assert mc.config.provider == "test"
        assert mc.config.model == "test-model"
        assert conversation.agent.model.effort == "max"
        assert conversation.ctx.model.effort == "max"

    def test_setting_the_default_writes_the_config(self, tmp_path: Path, make_mc):
        config = make_config()
        config.save = lambda *a, **k: None
        mc = make_mc(config)

        mc.set_default_model("second", "second-model")

        assert (config.provider, config.model) == ("second", "second-model")
        assert mc.new_conversation(cwd=tmp_path).model_name == "second-model"


# ── sessions ────────────────────────────────────────────────


class TestSessions:
    def test_save_records_the_conversation_or_nothing_when_nothing_was_said(
        self, mc: MoCode, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.messages.append({"role": "user", "content": "hello"})

        session = conversation.save()

        assert session is not None
        assert session.workdir == str(workdir)
        assert session.provider == "test"
        assert session.title == "hello"
        assert mc.store.find(conversation.id).messages == conversation.messages

        # 什么都没说：什么都不写
        silent = mc.new_conversation(cwd=project(tmp_path, "b"))
        assert silent.save() is None
        assert [s.id for s in mc.store.list_all()] == [conversation.id]

    async def test_start_begins_a_new_session_in_the_same_project(
        self, mc: MoCode, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.messages.append({"role": "user", "content": "old"})
        previous = conversation.id

        new_id = await conversation.new_session()

        assert new_id != previous
        assert conversation.messages == []
        assert [s.id for s in conversation.list_sessions()] == [previous]

        # 列表按项目分开：另一个项目的会话不混进这一个的列表
        other = mc.new_conversation(cwd=project(tmp_path, "b"))
        other.messages.append({"role": "user", "content": "b"})
        other.save()

        assert [s.title for s in conversation.list_sessions()] == ["old"]
        assert sorted(s.title for s in mc.store.list_all()) == ["b", "old"]

        # 新会话一张白纸：历史空了，插件消息也一样不带过去
        await conversation.ctx.emit_message("old/message", {})
        fresh_id = await conversation.new_session()
        assert fresh_id != previous
        assert conversation.session().plugin_messages == []

    async def test_resume_restores_the_history_and_the_model(
        self, mc: MoCode, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.set_model("second", "second-model")
        conversation.messages.append({"role": "user", "content": "remember me"})
        stored = conversation.save()

        fresh = mc.new_conversation(cwd=workdir)
        assert fresh.model_name == "test-model"

        await fresh.load_session(stored)

        assert fresh.messages == [{"role": "user", "content": "remember me"}]
        assert (fresh.provider_key, fresh.model_name) == ("second", "second-model")
        assert fresh.id == stored.id

        # 存档的 provider 已经不在了：历史照常恢复，模型留在当前这个——
        # 与上面同一个 resume 契约的另一个入口。
        gone = Session(
            id="session_gone",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir=str(workdir),
            messages=[{"role": "user", "content": "hi"}],
            provider="retired",
            model="old-model",
        )
        stranger = mc.new_conversation(cwd=workdir)
        await stranger.load_session(gone)

        assert stranger.model_name == "test-model"  # the current one stays
        assert stranger.messages == [{"role": "user", "content": "hi"}]

    async def test_the_runtime_finds_a_session_by_id_alone(
        self, mc: MoCode, tmp_path: Path
    ):
        workdir = project(tmp_path, "deep-project")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.messages.append({"role": "user", "content": "hello"})
        conversation.save()

        resumed = mc.resume(conversation.id)

        assert resumed is not None
        assert resumed.cwd == workdir
        assert resumed.messages == conversation.messages
        assert mc.resume("session_nope") is None


class TestPluginMessageReplay:
    """emit → save → resume → replay: the §5.4 promise, end to end."""

    async def test_saved_messages_replay_in_order_on_resume(
        self, mc: MoCode, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.messages.append({"role": "user", "content": "hi"})
        await conversation.ctx.emit_message(
            "rag/index", {"done": 12, "total": 40}, block_id="rag-1"
        )
        await conversation.ctx.emit_message(
            "rag/index", {"done": 40, "total": 40}, block_id="rag-1"
        )
        await conversation.ctx.seal_message("rag-1")
        await conversation.ctx.emit_message("shell/background-done", {"exit": 0})

        stored = conversation.save()
        assert stored is not None
        assert [
            (m["kind"], m["data"], m["block_id"], m["sealed"])
            for m in stored.plugin_messages
        ] == [
            ("rag/index", {"done": 12, "total": 40}, "rag-1", False),
            ("rag/index", {"done": 40, "total": 40}, "rag-1", False),
            ("", {}, "rag-1", True),
            ("shell/background-done", {"exit": 0}, "", False),
        ]

        fresh = mc.new_conversation(cwd=workdir)
        subscription = fresh.subscribe()
        await fresh.load_session(stored)

        events = _drain(subscription)
        # The redraw announcement comes first: replay lands on the cleared
        # document, never into one a reader is about to wipe.
        assert isinstance(events[0], ConversationChanged)
        replayed = [e for e in events if isinstance(e, PluginMessage)]
        assert [(e.kind, e.data, e.block_id, e.sealed) for e in replayed] == [
            ("rag/index", {"done": 12, "total": 40}, "rag-1", False),
            ("rag/index", {"done": 40, "total": 40}, "rag-1", False),
            ("", {}, "rag-1", True),
            ("shell/background-done", {"exit": 0}, "", False),
        ]

        # 重放不会被下一次 save 再次捕获：resume 之后再发一条，存档是
        # 原样重放的那几条加上新的一条——一条不多，一条不少。
        await fresh.ctx.emit_message("rag/index", {"done": 2})

        assert fresh.save().plugin_messages == [
            *stored.plugin_messages,
            {
                "type": "plugin_message",
                "run_id": "",
                "seq": fresh.agent.channel.seq,
                "kind": "rag/index",
                "data": {"done": 2},
                "block_id": "",
                "sealed": False,
            },
        ]

    async def test_replay_keeps_the_stored_run_id_and_restmps_seq(
        self, mc: MoCode, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        stored = Session(
            id="session_runid",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir=str(workdir),
            messages=[{"role": "user", "content": "hi"}],
            plugin_messages=[
                {
                    "type": "plugin_message",
                    "run_id": "run_old",
                    "seq": 7,
                    "kind": "k",
                    "data": {},
                    "block_id": "",
                    "sealed": False,
                }
            ],
        )

        fresh = mc.new_conversation(cwd=workdir)
        subscription = fresh.subscribe()
        await fresh.load_session(stored)

        events = _drain(subscription)
        [replayed] = [e for e in events if isinstance(e, PluginMessage)]
        assert replayed.run_id == "run_old"
        assert replayed.seq != 7
        assert replayed.seq > events[0].seq  # re-stamped, after the redraw

    async def test_only_the_newest_200_messages_survive_and_replay(
        self, mc: MoCode, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.messages.append({"role": "user", "content": "hi"})
        for i in range(250):
            await conversation.ctx.emit_message("progress", {"i": i})

        stored = conversation.save()
        assert len(stored.plugin_messages) == 200
        assert stored.plugin_messages[0]["data"] == {"i": 50}
        assert stored.plugin_messages[-1]["data"] == {"i": 249}

        fresh = mc.new_conversation(cwd=workdir)
        subscription = fresh.subscribe()
        await fresh.load_session(stored)
        replayed = [
            e for e in _drain(subscription) if isinstance(e, PluginMessage)
        ]
        assert [e.data["i"] for e in replayed] == list(range(50, 250))


# ── lifecycle ───────────────────────────────────────────────


class TestLifecycle:
    def test_close_saves_the_session_or_skips_it_when_asked(
        self, mc: MoCode, tmp_path: Path
    ):
        """close 保存现场并结束事件流；close(save=False) 不写盘，流照旧结束。"""
        workdir = project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.messages.append({"role": "user", "content": "bye"})

        conversation.close()

        assert mc.store.find(conversation.id) is not None
        assert conversation.subscribe().take() is None

        silent = mc.new_conversation(cwd=project(tmp_path, "b"))
        silent.messages.append({"role": "user", "content": "not saved"})

        silent.close(save=False)

        assert mc.store.find(silent.id) is None
        assert silent.subscribe().take() is None

    def test_rebuild_prompt_re_reads_the_project_and_clears_the_baseline(
        self, mc: MoCode, tmp_path: Path
    ):
        """AGENTS.md 是当场重读的文件：重建时用户级与项目级两份规则都重新
        注入 agents section。断言的是 section 在场、两份内容均到达——不钉任何
        一句产品文案。模型刚被重新告知一切：插件自己的基线状态不留在重建之后。
        """
        workdir = project(tmp_path, "a")
        mc.home.mkdir(parents=True, exist_ok=True)
        (mc.home / "AGENTS.md").write_text("user-level rule", encoding="utf-8")
        conversation = mc.new_conversation(cwd=workdir)
        conversation.rebuild_prompt()
        assert "user-level rule" in conversation.agent.system_prompt
        assert "project-level rule" not in conversation.agent.system_prompt

        conversation.ctx.plugin_state("demo")["n"] = 1

        (workdir / "AGENTS.md").write_text("project-level rule", encoding="utf-8")
        conversation.rebuild_prompt()

        prompt = conversation.agent.system_prompt
        assert "<agents>" in prompt  # 承载两份规则的 section 在场
        assert "user-level rule" in prompt
        assert "project-level rule" in prompt
        assert conversation.ctx.plugin_states == {}

    def test_an_imported_conversation_keeps_its_own_agent_config(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=project(tmp_path, "a"))
        assert conversation.agent.config == AgentConfig(
            tool_timeout=mc.config.agent.tool_timeout,
            max_iterations=mc.config.agent.max_iterations,
        )
        assert isinstance(conversation.model, ModelSpec)


class TestPluginsAreLoadedOnce:
    def test_a_project_loads_its_plugins_once(self, tmp_path: Path):
        """一个项目只加载一次：第二个 conversation 复用第一个加载的插件集。

        公开 seam 即可观察这件事——两个 conversation 的 host 持有同一个插件
        实例。没有模块级 ``plugin =`` 实例的插件每次加载都会重新构造，所以
        "重新加载过"必然是不同的对象；实例同一性即"只加载一次"的事实，无需读
        任何私有缓存、无需 patch 任何内部函数。默认目录（而非夹具的空列表）
        也正是被测对象：项目自己的 ``.mocode/plugins`` 如何解析。
        """
        mc = MoCode(config=make_config(), home=tmp_path / "home")
        workdir = project(tmp_path, "a")
        write_plugin(
            workdir / ".mocode" / "plugins",
            "greet",
            """
            from mocode.plugins import Plugin


            class Greet(Plugin):
                name = "greet"
            """,
        )

        first = mc.new_conversation(cwd=workdir)
        second = mc.new_conversation(cwd=workdir)

        loaded = next(p for p in mc.plugins_for(workdir) if p.name == "greet")
        hosts = [
            next(p for p in conversation.host.plugins if p.name == "greet")
            for conversation in (first, second)
        ]
        assert hosts[0] is hosts[1] is loaded

    def test_different_projects_load_their_own(self, tmp_path: Path):
        # the default dirs, not the fixture's empty list: this test is
        # about what a project's own .mocode/plugins resolves to.
        mc = MoCode(config=make_config(), home=tmp_path / "home")
        first = project(tmp_path, "a")
        second = project(tmp_path, "b")
        plugin = write_plugin(first / ".mocode" / "plugins", "greet")

        assert mc.plugin_sources_for(first) == [plugin]
        assert mc.plugin_sources_for(second) == []


# ── ending a conversation ──────────────────────────────────


class TestGracefulClose:
    async def test_aclose_delivers_the_ending_before_the_stream_closes(
        self, wired, tmp_path: Path
    ):
        """The full close: terminal event first, plugins released, then closed."""
        conversation, _ = wired(cwd=project(tmp_path, "a"))
        conversation.agent.provider = SlowProvider()
        reader = conversation.subscribe()
        turn = conversation.run("slow")

        await conversation.aclose()

        events = await collect(reader)
        assert terminal(events).cancelled is True
        assert turn.done and turn.cancelled
        assert conversation.agent.channel.closed


class TestTheSurfaceMaterializes:
    """The request surface — prompt + offered interface — is derived state:
    written in one place, once, before the first request (or from the session
    a resume carries). These are the contract's own tests."""

    def test_construction_is_cheap_and_writes_no_surface(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=project(tmp_path, "a"))

        assert conversation.agent.system_prompt == ""
        assert not conversation.tools.pinned
        assert "read" in conversation.tools.names()  # registrations still happen

    async def test_prepare_runs_each_plugins_prepare_once(
        self, tmp_path: Path, make_mc, wired
    ):
        """每个 conversation 的 prepare() 只跑一次：幂等，一次 turn 也不重跑。

        计数器是一个从插件目录加载的真插件——公开的发现/构建路径就是被测路径，
        无需清空任何私有缓存、无需替换任何内部函数。"""
        calls = tmp_path / "prepare-calls.txt"
        plugins_dir = tmp_path / "plugins"
        write_plugin(
            plugins_dir,
            "counting",
            f"""
            from pathlib import Path

            from mocode.plugins import Plugin


            class Counting(Plugin):
                name = "counting"

                async def prepare(self, ctx) -> None:
                    Path({str(calls)!r}).open("a").write("prepare\\n")
            """,
        )
        mc = make_mc(plugin_dirs=[plugins_dir])
        conversation, _ = wired("1", "2", cwd=project(tmp_path, "a"), mc=mc)
        await conversation.prepare()
        await conversation.prepare()  # idempotent
        await conversation.chat("hello")

        assert calls.read_text(encoding="utf-8") == "prepare\n"
        assert conversation.agent.system_prompt != ""
        assert conversation.tools.pinned

        # prompt 里最易腐烂的两节——time 与 environment——在场。断言 section
        # 名与日期的形状（``YYYY-MM-DD (Weekday)``），不断言某个具体真实日期：
        # 真实时钟的值会腐烂，形状不会。
        prompt = conversation.agent.system_prompt
        assert "<time>" in prompt and "</time>" in prompt
        assert "<environment>" in prompt
        assert re.search(r"\d{4}-\d{2}-\d{2} \(\w+\)", prompt)

    async def test_the_first_turn_carries_the_surface_with_no_notice(
        self, wired, tmp_path: Path
    ):
        conversation, _ = wired("one", "two", cwd=project(tmp_path, "a"))
        await conversation.chat("first")

        provider = conversation.agent.provider
        # The first request already ran on the materialized prompt and the
        # frozen interface — and nothing was announced to get it there.
        assert provider.calls[0]["system"] == conversation.agent.system_prompt
        assert "read" in {s["function"]["name"] for s in provider.calls[0]["tools"]}
        assert not [
            m for m in conversation.messages if "[context update" in str(m.get("content"))
        ]

    async def test_a_resume_is_not_re_materialized(self, wired, tmp_path: Path):
        workdir = project(tmp_path, "a")
        first, _ = wired("one", cwd=workdir)
        await first.chat("hi")
        session = first.save()
        frozen = first.agent.system_prompt

        second, _ = wired("two", cwd=workdir)
        await second.chat("again")

        assert second.agent.system_prompt == frozen
        assert session.system_prompt == frozen

        # 一个从没跑过的会话什么也不记录：没有 prompt 可恢复，resume 之后
        # 照样当场物化出一份
        never_ran, _ = wired(cwd=workdir)
        never_ran.messages.append({"role": "user", "content": "hi"})
        blank = never_ran.save()

        assert blank.system_prompt == ""  # it never ran; nothing to record

        fresh, _ = wired(cwd=workdir)
        await fresh.load_session(blank)
        # load_session restores the session's model, which replaces the
        # provider — the recorder goes back on afterwards.
        wire(fresh, "two")
        await fresh.chat("again")

        assert fresh.agent.system_prompt != ""

    async def test_an_unpinned_runtime_stays_live(self, tmp_path: Path, make_mc, wired):
        mc = make_mc(freeze_interface=False)
        conversation, _ = wired(
            "one", "two", cwd=project(tmp_path, "a"), mc=mc
        )
        await conversation.chat("first")
        conversation.tools.disable("read")
        await conversation.chat("second")

        provider = conversation.agent.provider
        assert "read" not in {s["function"]["name"] for s in provider.calls[1]["tools"]}
        assert conversation.agent.system_prompt != ""


class TestThePromptFreezesAcrossAResume:
    """A resume keeps the session's prompt byte-identical — the provider's
    prefix cache survives — and tells the model what changed instead."""

    async def test_a_resume_keeps_the_prompt_and_notices_drift(
        self, wired, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        (workdir / "AGENTS.md").write_text("version one", encoding="utf-8")
        first, _ = wired(cwd=workdir)
        await first.prepare()  # the session must record the surface it ran on
        first.messages.append({"role": "user", "content": "hi"})
        session = first.save()

        (workdir / "AGENTS.md").write_text("version two", encoding="utf-8")
        second, _ = wired(cwd=workdir)

        await second.load_session(session)

        # Frozen, not re-rendered: the old turns' prefix cache survives.
        assert second.agent.system_prompt == session.system_prompt
        notice = second.messages[-1]
        assert notice["role"] == "user"
        assert "[context update" in notice["content"]
        assert "version two" in notice["content"]
        resumed = second.session()
        assert resumed.system_prompt == session.system_prompt
        # The baseline the next notice diffs against now lives in the
        # plugin's own session state — and it says version two.
        assert "version two" in resumed.plugin_state["cache-protect"]["prompt"]

    async def test_an_unchanged_world_is_never_news(self, wired, tmp_path: Path):
        """什么都没变不是新闻；在 resume 看见之前改回去，同样不是。"""
        workdir = project(tmp_path, "a")
        (workdir / "AGENTS.md").write_text("version one", encoding="utf-8")
        first, _ = wired(cwd=workdir)
        await first.prepare()  # the session must record the surface it ran on
        first.messages.append({"role": "user", "content": "hi"})
        session = first.save()
        second, _ = wired(cwd=workdir)

        await second.load_session(session)

        assert second.messages == session.messages

        # Changed and reverted before any resume saw it: never announced.
        (workdir / "AGENTS.md").write_text("version two", encoding="utf-8")
        (workdir / "AGENTS.md").write_text("version one", encoding="utf-8")
        reverted, _ = wired(cwd=workdir)
        await reverted.load_session(reverted.list_sessions()[0])

        notices = [m for m in reverted.messages if "[context update" in str(m.get("content"))]
        assert notices == []

    async def test_a_revert_after_a_notice_is_news_again(
        self, wired, tmp_path: Path
    ):
        workdir = project(tmp_path, "a")
        (workdir / "AGENTS.md").write_text("version one", encoding="utf-8")
        first, _ = wired(cwd=workdir)
        await first.prepare()  # the session must record the surface it ran on
        first.messages.append({"role": "user", "content": "hi"})
        first.save()

        (workdir / "AGENTS.md").write_text("version two", encoding="utf-8")
        second, _ = wired(cwd=workdir)
        await second.load_session(second.list_sessions()[0])
        resumed = second.save()

        (workdir / "AGENTS.md").write_text("version one", encoding="utf-8")
        third, _ = wired(cwd=workdir)
        await third.load_session(resumed)

        # The baseline is what the model was last told (version two), so the
        # revert back to version one is news it must hear.
        still = [m for m in third.messages if "[context update" in str(m.get("content"))]
        assert len(still) == 2
        assert "version one" in still[-1]["content"]


class TestPluginStateTravels:
    """A plugin's own state is the host's to carry and the plugin's to fill —
    the one thing a plugin could not do before: remember across a resume."""

    def test_it_is_written_on_save_and_arrives_on_resume(
        self, mc: MoCode, tmp_path: Path
    ):
        """插件的自有状态随会话存档带出，再由 resume 带回来。"""
        first = mc.new_conversation(cwd=project(tmp_path, "a"))
        first.messages.append({"role": "user", "content": "hi"})
        first.ctx.plugin_state("demo")["n"] = 7
        session = first.save()

        assert session.plugin_state["demo"]["n"] == 7

        second = mc.resume(session.id)

        assert second.ctx.plugin_state("demo") == {"n": 7}

    async def test_load_session_swaps_the_state(self, mc: MoCode, tmp_path: Path):
        first = mc.new_conversation(cwd=project(tmp_path, "a"))
        first.messages.append({"role": "user", "content": "hi"})
        first.ctx.plugin_state("demo")["n"] = 7
        session = first.save()

        second = mc.new_conversation(cwd=project(tmp_path, "b"))
        second.ctx.plugin_state("demo")["n"] = 1

        await second.load_session(session)

        assert second.ctx.plugin_state("demo") == {"n": 7}

"""Conversations — many at once, in different projects, on different models.

This is what the runtime exists for: one process holding N conversations that
share the config, the plugin loading and the session store, and share nothing
else. The tests here are the ones that would have failed before the runtime
grew a conversation object: two turns running at once, two projects with their
own working directory, two histories, two models.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mocode.core.agent import AgentConfig
from mocode.core.events import Notice, RunFinished, TextDelta
from mocode.core.provider import ModelSpec, Response, Usage
from mocode.host.runtime import MoCode
from mocode.host.session import Session

from .conftest import make_config
from .providers import MockProvider, SlowProvider, tool_call_response


def _answer(text: str = "done") -> Response:
    return Response(content=text, usage=Usage(1, 1), finish_reason="stop")


def _project(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conversation(mc: MoCode, cwd: Path, *responses: Response, chunk_size: int = 0):
    conversation = mc.new_conversation(cwd=cwd)
    conversation.agent.provider = MockProvider(
        list(responses) or [_answer()], chunk_size=chunk_size
    )
    return conversation


# ── the conversation as a unit ──────────────────────────────


class TestAConversationIsItsOwn:
    def test_it_carries_its_own_project_and_identity(self, mc: MoCode, tmp_path: Path):
        first = mc.new_conversation(cwd=_project(tmp_path, "a"))
        second = mc.new_conversation(cwd=_project(tmp_path, "b"))

        assert first.cwd != second.cwd
        assert first.id != second.id
        assert first.id.startswith("session_")

    def test_histories_do_not_mix(self, mc: MoCode, tmp_path: Path):
        first = mc.new_conversation(cwd=_project(tmp_path, "a"))
        second = mc.new_conversation(cwd=_project(tmp_path, "b"))

        first.messages.append({"role": "user", "content": "hello from a"})

        assert second.messages == []

    @pytest.mark.asyncio
    async def test_the_system_prompt_names_the_project(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=project)
        await conversation.prepare()

        assert f"cwd: {project}" in conversation.agent.system_prompt

    def test_tool_registries_are_not_shared(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        first = mc.new_conversation(cwd=project)
        second = mc.new_conversation(cwd=project)

        assert first.tools.get("bash") is not second.tools.get("bash")
        assert first.commands is not second.commands


class TestToolsWorkInTheConversationsProject:
    @pytest.mark.asyncio
    async def test_bash_starts_in_the_project(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=project)

        result = await conversation.tools.get("bash").run_async(
            {"command": "pwd"}, None
        )

        assert project.name in result.content

    @pytest.mark.asyncio
    async def test_bash_state_does_not_leak_between_conversations(
        self, mc: MoCode, tmp_path: Path
    ):
        # Distinct words: a shell reports its directory in its own notation
        # (MSYS form under Git Bash on Windows), so compare by name.
        project = _project(tmp_path, "alpha")
        elsewhere = _project(tmp_path, "beta")
        first = mc.new_conversation(cwd=project)
        second = mc.new_conversation(cwd=project)
        bash = first.tools.get("bash")

        await bash.run_async({"command": f"cd {elsewhere}"}, None)
        await bash.run_async({"command": "export LEAK=1"}, None)

        there = await second.tools.get("bash").run_async({"command": "pwd"}, None)
        env = await second.tools.get("bash").run_async({"command": "echo $LEAK"}, None)

        assert "alpha" in there.content
        assert "beta" not in there.content
        assert env.content == "(empty)"

    def test_relative_paths_resolve_into_the_project(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        (project / "notes.txt").write_text("hello", encoding="utf-8")
        conversation = mc.new_conversation(cwd=project)

        result = conversation.tools.get("read").run({"path": "notes.txt"})

        assert "hello" in result.content

    def test_skills_come_from_the_project(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        skill = project / ".mocode" / "skills" / "deploy"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: ship it\n---\n\nRun the deploy.",
            encoding="utf-8",
        )

        conversation = mc.new_conversation(cwd=project)

        assert "/skill:deploy" in {c.name for c in conversation.commands.all()}


# ── concurrency ─────────────────────────────────────────────


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_two_conversations_run_at_the_same_time(self, mc: MoCode, tmp_path: Path):
        async def slow(args, ctx):
            await asyncio.sleep(0.05)
            return "slept"

        from mocode.core.tool import Tool

        first = _conversation(
            mc,
            _project(tmp_path, "a"),
            tool_call_response("wait"),
            _answer("first done"),
        )
        second = _conversation(mc, _project(tmp_path, "b"), _answer("second done"))
        first.tools.register(Tool("wait", "d", {}, slow))

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

    @pytest.mark.asyncio
    async def test_a_second_turn_in_one_conversation_is_refused(
        self, mc: MoCode, tmp_path: Path
    ):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        conversation.agent.provider = SlowProvider()

        turn = conversation.run("hi")
        assert conversation.busy
        with pytest.raises(RuntimeError, match="already running"):
            conversation.run("again")

        conversation.cancel()
        assert (await turn.wait()).cancelled

    @pytest.mark.asyncio
    async def test_stopping_one_conversation_leaves_the_other_alone(
        self, mc: MoCode, tmp_path: Path
    ):
        stopped = mc.new_conversation(cwd=_project(tmp_path, "a"))
        stopped.agent.provider = SlowProvider()
        running = _conversation(mc, _project(tmp_path, "b"), _answer("still here"))

        turn = stopped.run("hi")
        await asyncio.sleep(0.01)
        stopped.cancel()

        assert (await turn.wait()).cancelled
        assert await running.chat("hello") == "still here"

    @pytest.mark.asyncio
    async def test_a_reader_can_watch_a_conversation_it_did_not_start(
        self, mc: MoCode, tmp_path: Path
    ):
        conversation = _conversation(mc, _project(tmp_path, "a"), _answer("watched"))
        reader = conversation.subscribe()

        await conversation.chat("hi")

        seen = []
        while True:
            event = reader.take()
            if event is None:
                break
            seen.append(event)
        assert isinstance(seen[-1], RunFinished)
        assert seen[-1].content == "watched"

    @pytest.mark.asyncio
    async def test_a_notice_between_turns_reaches_readers(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        reader = conversation.subscribe()

        await conversation.notify("something happened", level="warn")

        event = reader.take()
        assert isinstance(event, Notice)
        assert (event.message, event.level) == ("something happened", "warn")


# ── the model is per conversation ───────────────────────────


class TestModel:
    def test_set_model_changes_only_this_conversation(self, mc: MoCode, tmp_path: Path):
        first = mc.new_conversation(cwd=_project(tmp_path, "a"))
        second = mc.new_conversation(cwd=_project(tmp_path, "b"))

        first.set_model("second", "second-model")

        assert (first.provider_key, first.model_name) == ("second", "second-model")
        assert (second.provider_key, second.model_name) == ("test", "test-model")
        assert first.agent.provider is not second.agent.provider

    def test_set_model_does_not_touch_the_config(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        mc.config.save = lambda *a, **k: pytest.fail("switching a model is not a config write")

        conversation.set_model("second", "second-model")

        assert mc.config.active_provider == "test"
        assert mc.config.active_model == "test-model"

    def test_a_conversation_can_override_the_default(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(
            cwd=_project(tmp_path, "a"), provider="second", model="second-model"
        )
        assert conversation.model_name == "second-model"
        assert conversation.model == mc.config.model_spec("second", "second-model")

    def test_an_unknown_provider_is_reported(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        with pytest.raises(ValueError, match="not defined"):
            conversation.set_model("nope", "m")

    def test_setting_the_default_writes_the_config(self, tmp_path: Path):
        config = make_config()
        config.save = lambda *a, **k: None
        mc = MoCode(config=config, home=tmp_path / "home", plugin_dirs=[])

        mc.set_default_model("second", "second-model")

        assert (config.active_provider, config.active_model) == ("second", "second-model")
        assert mc.new_conversation(cwd=tmp_path).model_name == "second-model"


# ── sessions ────────────────────────────────────────────────


class TestSessions:
    def test_save_records_the_conversation(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=project)
        conversation.messages.append({"role": "user", "content": "hello"})

        session = conversation.save()

        assert session is not None
        assert session.workdir == str(project)
        assert session.provider == "test"
        assert session.title == "hello"
        assert mc.store.load(str(project), conversation.id).messages == conversation.messages

    def test_nothing_said_means_nothing_written(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        assert conversation.save() is None
        assert mc.store.list_all() == []

    @pytest.mark.asyncio
    async def test_start_begins_a_new_session_in_the_same_project(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=project)
        conversation.messages.append({"role": "user", "content": "old"})
        previous = conversation.id

        new_id = await conversation.new_session()

        assert new_id != previous
        assert conversation.messages == []
        assert [s.id for s in conversation.list_sessions()] == [previous]

    @pytest.mark.asyncio
    async def test_resume_restores_the_history_and_the_model(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=project)
        conversation.set_model("second", "second-model")
        conversation.messages.append({"role": "user", "content": "remember me"})
        stored = conversation.save()

        fresh = mc.new_conversation(cwd=project)
        assert fresh.model_name == "test-model"

        await fresh.load_session(stored)

        assert fresh.messages == [{"role": "user", "content": "remember me"}]
        assert (fresh.provider_key, fresh.model_name) == ("second", "second-model")
        assert fresh.id == stored.id

    @pytest.mark.asyncio
    async def test_a_resumed_session_keeps_its_model_even_if_the_provider_is_gone(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        stored = Session(
            id="session_gone",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            workdir=str(project),
            messages=[{"role": "user", "content": "hi"}],
            provider="retired",
            model="old-model",
        )

        conversation = mc.new_conversation(cwd=project)
        await conversation.load_session(stored)

        assert conversation.model_name == "test-model"  # the current one stays
        assert conversation.messages == [{"role": "user", "content": "hi"}]

    def test_sessions_are_listed_per_project(self, mc: MoCode, tmp_path: Path):
        first = mc.new_conversation(cwd=_project(tmp_path, "a"))
        second = mc.new_conversation(cwd=_project(tmp_path, "b"))
        first.messages.append({"role": "user", "content": "a"})
        second.messages.append({"role": "user", "content": "b"})
        first.save()
        second.save()

        assert [s.title for s in first.list_sessions()] == ["a"]
        assert [s.title for s in mc.store.list_all()] == ["b", "a"] or sorted(
            s.title for s in mc.store.list_all()
        ) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_the_runtime_finds_a_session_by_id_alone(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "deep-project")
        conversation = mc.new_conversation(cwd=project)
        conversation.messages.append({"role": "user", "content": "hello"})
        conversation.save()

        resumed = mc.resume(conversation.id)

        assert resumed is not None
        assert resumed.cwd == project
        assert resumed.messages == conversation.messages
        assert mc.resume("session_nope") is None


# ── lifecycle ───────────────────────────────────────────────


class TestLifecycle:
    def test_close_saves_and_ends_the_stream(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=project)
        conversation.messages.append({"role": "user", "content": "bye"})

        conversation.close()

        assert mc.store.load(str(project), conversation.id) is not None
        assert conversation.subscribe().take() is None

    def test_close_can_skip_saving(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        conversation.messages.append({"role": "user", "content": "not saved"})

        conversation.close(save=False)

        assert mc.store.list_all() == []

    def test_rebuild_prompt_re_reads_the_project(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        conversation = mc.new_conversation(cwd=project)
        assert "Always use tabs." not in conversation.agent.system_prompt

        (project / "AGENTS.md").write_text("Always use tabs.", encoding="utf-8")

        conversation.rebuild_prompt()

        assert "Always use tabs." in conversation.agent.system_prompt

    def test_an_imported_conversation_keeps_its_own_agent_config(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        assert conversation.agent.config == AgentConfig(
            tool_timeout=mc.config.agent.tool_timeout,
            max_iterations=mc.config.agent.max_iterations,
        )
        assert isinstance(conversation.model, ModelSpec)


class TestPluginsAreLoadedOnce:
    def test_a_project_loads_its_plugins_once(self, tmp_path: Path, monkeypatch):
        from mocode.host import runtime as runtime_module

        calls: list[Path] = []
        real = runtime_module.load_plugins

        def counting(*, plugin_dirs, config, reserved=()):
            calls.append(list(plugin_dirs)[0])
            return real(plugin_dirs=plugin_dirs, config=config, reserved=reserved)

        monkeypatch.setattr(runtime_module, "load_plugins", counting)
        mc = MoCode(config=make_config(), home=tmp_path / "home")
        project = _project(tmp_path, "a")

        mc.new_conversation(cwd=project)
        mc.new_conversation(cwd=project)

        assert len(calls) == 1

    def test_different_projects_load_their_own(self, tmp_path: Path):
        mc = MoCode(config=make_config(), home=tmp_path / "home")
        first = _project(tmp_path, "a")
        second = _project(tmp_path, "b")
        plugin = first / ".mocode" / "plugins" / "greet"
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text('{"name": "greet"}', encoding="utf-8")

        assert mc.plugin_sources_for(first) == [plugin]
        assert mc.plugin_sources_for(second) == []


# ── ending a conversation ──────────────────────────────────


class TestGracefulClose:
    @pytest.mark.asyncio
    async def test_a_new_session_is_refused_while_a_turn_runs(
        self, mc: MoCode, tmp_path: Path
    ):
        conversation = _conversation(mc, _project(tmp_path, "a"))
        conversation.agent.provider = SlowProvider()
        turn = conversation.run("slow")

        with pytest.raises(RuntimeError, match="cancel it first"):
            await conversation.new_session()

        turn.cancel()
        await turn.wait()

    @pytest.mark.asyncio
    async def test_aclose_delivers_the_ending_before_the_stream_closes(
        self, mc: MoCode, tmp_path: Path
    ):
        """The full close: terminal event first, plugins released, then closed."""
        conversation = _conversation(mc, _project(tmp_path, "a"))
        conversation.agent.provider = SlowProvider()
        reader = conversation.subscribe()
        turn = conversation.run("slow")

        await conversation.aclose()

        events = []
        while True:
            event = await reader.get()
            if event is None:
                break
            events.append(event)
        assert isinstance(events[-1], RunFinished)
        assert events[-1].cancelled is True
        assert turn.done and turn.cancelled
        assert conversation.agent.channel.closed


class TestTheSurfaceMaterializes:
    """The request surface — prompt + offered interface — is derived state:
    written in one place, once, before the first request (or from the session
    a resume carries). These are the contract's own tests."""

    def test_construction_is_cheap_and_writes_no_surface(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))

        assert conversation.agent.system_prompt == ""
        assert not conversation.tools.pinned
        assert "read" in conversation.tools.names()  # registrations still happen

    @pytest.mark.asyncio
    async def test_prepare_runs_each_plugins_prepare_once(self, mc: MoCode, tmp_path: Path):
        calls: list[str] = []
        from mocode.host.plugin.base import Plugin

        class Counting(Plugin):
            name = "counting"

            async def prepare(self, ctx) -> None:
                calls.append("prepare")

        mc._plugins.clear()
        original = mc._loaded_for

        def _with_counting(cwd):
            loaded = original(cwd)
            loaded.plugins.append(Counting())
            return loaded

        mc._loaded_for = _with_counting  # type: ignore[method-assign]
        try:
            conversation = _conversation(mc, _project(tmp_path, "a"), _answer("1"), _answer("2"))
            await conversation.prepare()
            await conversation.prepare()  # idempotent
            await conversation.chat("hello")

            assert calls == ["prepare"]
            assert conversation.agent.system_prompt != ""
            assert conversation.tools.pinned
        finally:
            mc._loaded_for = original  # type: ignore[method-assign]

    @pytest.mark.asyncio
    async def test_the_first_turn_carries_the_surface_with_no_notice(
        self, mc: MoCode, tmp_path: Path
    ):
        conversation = _conversation(mc, _project(tmp_path, "a"), _answer("one"), _answer("two"))
        await conversation.chat("first")

        provider = conversation.agent.provider
        # The first request already ran on the materialized prompt and the
        # frozen interface — and nothing was announced to get it there.
        assert provider.calls[0]["system"] == conversation.agent.system_prompt
        assert "read" in {s["function"]["name"] for s in provider.calls[0]["tools"]}
        assert not [
            m for m in conversation.messages if "[context update" in str(m.get("content"))
        ]

    @pytest.mark.asyncio
    async def test_a_resume_is_not_re_materialized(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        first = _conversation(mc, project, _answer("one"))
        await first.chat("hi")
        session = first.save()
        frozen = first.agent.system_prompt

        second = _conversation(mc, project, _answer("two"))
        await second.chat("again")

        assert second.agent.system_prompt == frozen
        assert session.system_prompt == frozen

    @pytest.mark.asyncio
    async def test_a_never_run_session_materializes_freshly_on_resume(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        conversation = _conversation(mc, project)
        conversation.messages.append({"role": "user", "content": "hi"})
        stored = conversation.save()

        assert stored.system_prompt == ""  # it never ran; nothing to record

        fresh = _conversation(mc, project)
        await fresh.load_session(stored)
        # load_session restores the session's model, which replaces the
        # provider — the recorder goes back on afterwards.
        fresh.agent.provider = MockProvider([_answer("two")])
        await fresh.chat("again")

        assert fresh.agent.system_prompt != ""

    @pytest.mark.asyncio
    async def test_an_unpinned_runtime_stays_live(self, tmp_path: Path):
        mc = MoCode(config=make_config(), home=tmp_path / "home", freeze_interface=False,
                    plugin_dirs=[])
        conversation = _conversation(
            mc, _project(tmp_path, "a"), _answer("one"), _answer("two")
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

    @pytest.mark.asyncio
    async def test_the_prompt_carries_time_and_os(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        await conversation.prepare()

        assert "today:" in conversation.agent.system_prompt
        assert "os:" in conversation.agent.system_prompt

    @pytest.mark.asyncio
    async def test_a_resume_keeps_the_prompt_and_notices_drift(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        (project / "AGENTS.md").write_text("version one", encoding="utf-8")
        first = _conversation(mc, project)
        await first.prepare()  # the session must record the surface it ran on
        first.messages.append({"role": "user", "content": "hi"})
        session = first.save()

        (project / "AGENTS.md").write_text("version two", encoding="utf-8")
        second = _conversation(mc, project)

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

    @pytest.mark.asyncio
    async def test_no_drift_means_no_notice(self, mc: MoCode, tmp_path: Path):
        project = _project(tmp_path, "a")
        first = _conversation(mc, project)
        await first.prepare()  # the session must record the surface it ran on
        first.messages.append({"role": "user", "content": "hi"})
        session = first.save()
        second = _conversation(mc, project)

        await second.load_session(session)

        assert second.messages == session.messages

    @pytest.mark.asyncio
    async def test_a_change_reverted_between_resumes_is_never_news(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        (project / "AGENTS.md").write_text("version one", encoding="utf-8")
        first = _conversation(mc, project)
        await first.prepare()  # the session must record the surface it ran on
        first.messages.append({"role": "user", "content": "hi"})
        first.save()

        # Changed and reverted before any resume saw it: never announced.
        (project / "AGENTS.md").write_text("version two", encoding="utf-8")
        (project / "AGENTS.md").write_text("version one", encoding="utf-8")
        second = _conversation(mc, project)
        await second.load_session(second.list_sessions()[0])

        notices = [m for m in second.messages if "[context update" in str(m.get("content"))]
        assert notices == []

    @pytest.mark.asyncio
    async def test_a_revert_after_a_notice_is_news_again(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        (project / "AGENTS.md").write_text("version one", encoding="utf-8")
        first = _conversation(mc, project)
        await first.prepare()  # the session must record the surface it ran on
        first.messages.append({"role": "user", "content": "hi"})
        first.save()

        (project / "AGENTS.md").write_text("version two", encoding="utf-8")
        second = _conversation(mc, project)
        await second.load_session(second.list_sessions()[0])
        resumed = second.save()

        (project / "AGENTS.md").write_text("version one", encoding="utf-8")
        third = _conversation(mc, project)
        await third.load_session(resumed)

        # The baseline is what the model was last told (version two), so the
        # revert back to version one is news it must hear.
        still = [m for m in third.messages if "[context update" in str(m.get("content"))]
        assert len(still) == 2
        assert "version one" in still[-1]["content"]

    @pytest.mark.asyncio
    async def test_a_legacy_session_gets_the_current_prompt(
        self, mc: MoCode, tmp_path: Path
    ):
        project = _project(tmp_path, "a")
        conversation = _conversation(mc, project)
        conversation.messages.append({"role": "user", "content": "hi"})
        stored = conversation.save()
        legacy = Session(
            id=stored.id,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            workdir=stored.workdir,
            messages=stored.messages,
            title=stored.title,
            model=stored.model,
            provider=stored.provider,
        )
        fresh = _conversation(mc, project)
        await fresh.prepare()
        before = fresh.agent.system_prompt

        await fresh.load_session(legacy)

        assert fresh.agent.system_prompt == before
        assert fresh.messages == legacy.messages


class TestPluginStateTravels:
    """A plugin's own state is the host's to carry and the plugin's to fill —
    the one thing a plugin could not do before: remember across a resume."""

    def test_it_is_written_on_save(self, mc: MoCode, tmp_path: Path):
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        conversation.messages.append({"role": "user", "content": "hi"})
        conversation.ctx.plugin_state("demo")["n"] = 1

        session = conversation.save()

        assert session.plugin_state["demo"]["n"] == 1

    @pytest.mark.asyncio
    async def test_a_resume_arrives_with_the_sessions_state(
        self, mc: MoCode, tmp_path: Path
    ):
        first = mc.new_conversation(cwd=_project(tmp_path, "a"))
        first.messages.append({"role": "user", "content": "hi"})
        first.ctx.plugin_state("demo")["n"] = 7
        session = first.save()

        second = mc.resume(session.id)

        assert second.ctx.plugin_state("demo") == {"n": 7}

    @pytest.mark.asyncio
    async def test_load_session_swaps_the_state(self, mc: MoCode, tmp_path: Path):
        first = mc.new_conversation(cwd=_project(tmp_path, "a"))
        first.messages.append({"role": "user", "content": "hi"})
        first.ctx.plugin_state("demo")["n"] = 7
        session = first.save()

        second = mc.new_conversation(cwd=_project(tmp_path, "b"))
        second.ctx.plugin_state("demo")["n"] = 1

        await second.load_session(session)

        assert second.ctx.plugin_state("demo") == {"n": 7}

    def test_a_rebuild_clears_it(self, mc: MoCode, tmp_path: Path):
        """The model was just re-told everything — no baseline survives it."""
        conversation = mc.new_conversation(cwd=_project(tmp_path, "a"))
        conversation.ctx.plugin_state("demo")["n"] = 1

        conversation.rebuild_prompt()

        assert conversation.ctx.plugin_states == {}

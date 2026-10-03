"""ToolDispatcher — the one execution path, for the loop and for programs."""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest

from mocode.core.agent import AgentConfig, AgentLoop
from mocode.core.dispatch import DispatchResult, ToolDispatcher
from mocode.core.events import Event, Notice, ToolCallFinished, ToolCallStarted
from mocode.core.hook import AgentHook, HookRunner, IterationContext, ToolCallContext
from mocode.core.provider import Response, Usage
from mocode.core.tool import (
    Tool,
    ToolConflictError,
    ToolError,
    ToolPolicy,
    ToolRegistry,
    ToolResult,
)
from mocode.host.config import Config
from mocode.host.plugin.base import Plugin
from mocode.host.plugin.context import BuildContext, HostContext
from mocode.host.plugin.host import PluginHost, load_plugins
from mocode.testing import MockProvider, collect, say, tool_call_response

from .conftest import echo_tool, make_agent, write_plugin


def _failing(exc: Exception) -> Tool:
    def run(args):
        raise exc

    return Tool("boom", "d", {}, run)


def _sleeper() -> Tool:
    return Tool("slow", "d", {}, lambda a: time.sleep(1))


def _sink(events: list[Event], folds: list[bool]):
    """A publish callback that records what the dispatcher sent — bare core."""

    async def publish(event: Event, *, fold: bool) -> None:
        events.append(event)
        folds.append(fold)

    return publish


def _bare_dispatcher(
    *tools: Tool,
    hooks: list[AgentHook] | None = None,
    config: AgentConfig | None = None,
) -> tuple[ToolDispatcher, list[Event], list[bool]]:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    events: list[Event] = []
    folds: list[bool] = []
    dispatcher = ToolDispatcher(
        registry, HookRunner(hooks or []), config or AgentConfig(), _sink(events, folds)
    )
    return dispatcher, events, folds


class _Denier(AgentHook):
    def __init__(self, reason: str = "not allowed") -> None:
        self.reason = reason

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        ctx.deny = self.reason


# ── bare core: the dispatcher without a loop ─────────────────


class TestBareCoreDispatcher:
    async def test_a_call_runs_the_tool_and_publishes_its_events(self):
        dispatcher, events, _ = _bare_dispatcher(echo_tool())

        result = await dispatcher.run("echo", {"value": "x"}, call_id="c1")

        assert isinstance(result, DispatchResult)
        assert (result.status, result.content, result.call_id) == ("ok", "echo:x", "c1")
        assert [type(e) for e in events] == [ToolCallStarted, ToolCallFinished]
        assert events[0].args == {"value": "x"}
        assert events[1].status == "ok"

    async def test_model_origin_events_fold_program_origin_events_do_not(self):
        tool = echo_tool()

        model, _, model_folds = _bare_dispatcher(tool)
        await model.run("echo", {"value": "x"})
        assert model_folds == [True, True]

        program, _, program_folds = _bare_dispatcher(tool)
        await program.run("echo", {"value": "x"}, origin="program")
        assert program_folds == [False, False]

    async def test_structured_details_travel_on_the_result(self):
        tool = Tool("stats", "d", {}, lambda a: ToolResult("read it", {"lines": 412}))

        dispatcher, events, _ = _bare_dispatcher(tool)
        result = await dispatcher.run("stats", {})

        assert result.details == {"lines": 412}
        assert events[1].details == {"lines": 412}
        assert result.content == "read it"

    async def test_results_are_truncated_to_the_configured_limit(self):
        tool = Tool("big", "d", {}, lambda a: "x" * 100)

        dispatcher, _, _ = _bare_dispatcher(
            tool, config=AgentConfig(tool_result_limit=10)
        )
        result = await dispatcher.run("big", {})

        assert result.content == "x" * 10 + "\n... [truncated]"

    async def test_a_per_call_timeout_overrides_the_config(self):
        dispatcher, _, _ = _bare_dispatcher(_sleeper(), config=AgentConfig(tool_timeout=30))
        result = await dispatcher.run("slow", {}, timeout=0.05)

        assert result.status == "timeout"
        assert result.content.startswith("timeout:")


# ── execution policy: call over tool over config ────────────


class TestToolPolicy:
    @pytest.mark.parametrize(
        "policy,call_timeout,config_timeout",
        [
            # tool 策略压过 config
            (ToolPolicy(timeout=0.05), None, 30),
            # 调用级再压过 tool 策略
            (ToolPolicy(timeout=30), 0.05, 30),
            # 策略对超时不表态，config 的 0.05s 生效
            (ToolPolicy(), None, 0.05),
        ],
    )
    async def test_the_effective_timeout_is_call_over_tool_over_config(
        self, policy, call_timeout, config_timeout
    ):
        slow = Tool("slow", "d", {}, lambda a: time.sleep(1), policy=policy)
        dispatcher, _, _ = _bare_dispatcher(
            slow, config=AgentConfig(tool_timeout=config_timeout)
        )

        result = await dispatcher.run("slow", {}, timeout=call_timeout)

        assert result.status == "timeout"
        assert "0.05" in result.content  # 生效的那个超时值落在结果文案里

    async def test_a_policy_result_limit_overrides_the_config(self):
        big = Tool("big", "d", {}, lambda a: "x" * 100, policy=ToolPolicy(result_limit=10))
        dispatcher, _, _ = _bare_dispatcher(big, config=AgentConfig(tool_result_limit=50))

        result = await dispatcher.run("big", {})

        assert result.content == "x" * 10 + "\n... [truncated]"

    async def test_a_callable_policy_reads_the_call_arguments(self):
        seen: list[dict] = []

        def policy(args: dict) -> ToolPolicy:
            seen.append(dict(args))
            return ToolPolicy(timeout=args.get("t"))

        schema = {
            "type": "object",
            "properties": {"t": {"type": "number", "description": "deadline"}},
        }
        slow = Tool("slow", "d", schema, lambda a: time.sleep(1), policy=policy)
        dispatcher, _, _ = _bare_dispatcher(slow, config=AgentConfig(tool_timeout=30))

        result = await dispatcher.run("slow", {"t": 0.05})

        assert result.status == "timeout"
        assert seen == [{"t": 0.05}]

    async def test_the_effective_values_land_on_the_context(self):
        seen: dict = {}

        class Reader(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                seen["start"] = (ctx.tool_timeout, ctx.tool_result_limit)

            async def on_tool_complete(self, ctx: ToolCallContext) -> None:
                seen["complete"] = (ctx.tool_timeout, ctx.tool_result_limit)

        tool = Tool("ok", "d", {}, lambda a: "fine", policy=ToolPolicy(result_limit=7))
        dispatcher, _, _ = _bare_dispatcher(
            tool, hooks=[Reader()], config=AgentConfig(tool_timeout=30, tool_result_limit=500)
        )

        await dispatcher.run("ok", {})

        # on_tool_start sees the config-level values (the tool policy resolves
        # after it, in the execution step); on_tool_complete sees the effective
        # ones the call actually ran under.
        assert seen["start"] == (30, 500)
        assert seen["complete"] == (30, 7)


# ── call identity ────────────────────────────────────────────


class TestCallIdentity:
    async def test_program_calls_nested_in_a_parent_are_numbered_per_parent(self):
        dispatcher, events, _ = _bare_dispatcher(echo_tool())

        await dispatcher.run("echo", {}, origin="program", parent_call_id="p9")
        await dispatcher.run("echo", {}, origin="program", parent_call_id="p9")
        await dispatcher.run("echo", {}, origin="program", parent_call_id="other")

        ids = [e.call_id for e in events if isinstance(e, ToolCallStarted)]
        assert ids == ["p9:1", "p9:2", "other:1"]

    async def test_a_parentless_program_call_gets_a_pcall_id(self):
        dispatcher, events, _ = _bare_dispatcher(echo_tool())

        await dispatcher.run("echo", {}, origin="program")
        await dispatcher.run("echo", {}, origin="program")

        ids = [e.call_id for e in events if isinstance(e, ToolCallStarted)]
        assert ids == ["pcall_1", "pcall_2"]

    async def test_model_calls_keep_the_provider_id_or_get_one_made_up(self):
        dispatcher, events, _ = _bare_dispatcher(echo_tool())

        await dispatcher.run("echo", {}, call_id="from-provider")
        await dispatcher.run("echo", {})

        ids = [e.call_id for e in events if isinstance(e, ToolCallStarted)]
        assert ids == ["from-provider", "call_2"]


# ── outcome parity: the origins cannot drift ─────────────────


class TestOutcomeParity:
    """Deny, timeout and every error mean the same thing whatever the origin."""

    @pytest.mark.parametrize(
        "name,tool,kwargs,expected",
        [
            (
                "boom",
                _failing(RuntimeError("kaboom")),
                {},
                ("error", "error: kaboom", None),
            ),
            (
                "boom",
                _failing(ToolError("nope", "teapot")),
                {},
                ("error", "error: teapot: nope", "teapot"),
            ),
            (
                "ghost",
                echo_tool(),
                {},
                ("not_found", "error: unknown tool 'ghost'", None),
            ),
        ],
    )
    async def test_outcomes_are_identical_across_origins(
        self, name, tool, kwargs, expected
    ):
        outcomes = {}
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(tool)
            result = await dispatcher.run(name, {}, origin=origin, **kwargs)
            outcomes[origin] = (result.status, result.content, result.error_code)
        assert outcomes["model"] == outcomes["program"] == expected

    async def test_a_vetoed_call_is_denied_for_both_origins(self):
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(echo_tool(), hooks=[_Denier("no")])
            result = await dispatcher.run("echo", {"value": "x"}, origin=origin)
            assert (result.status, result.content) == ("denied", "denied: no")

    async def test_a_timeout_is_a_timeout_whatever_the_origin(self):
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(
                _sleeper(), config=AgentConfig(tool_timeout=0.05)
            )
            result = await dispatcher.run("slow", {}, origin=origin)
            assert result.status == "timeout"
            assert result.content.startswith("timeout:")

    async def test_a_switched_off_tool_refuses_for_both_origins(self):
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(echo_tool())
            dispatcher.registry.disable("echo")
            result = await dispatcher.run("echo", {"value": "x"}, origin=origin)
            assert result.status == "denied"
            assert "switched off" in result.content


# ── visibility by audience ───────────────────────────────────


class TestAvailability:
    """Three deployment shapes, one mechanism — who a tool is for."""

    @staticmethod
    def _tool(name: str, availability: str = "both") -> Tool:
        return Tool(name, "d", {}, lambda a: f"ran:{name}", availability=availability)

    @staticmethod
    def _schema_names(registry: ToolRegistry, **kwargs) -> list[str]:
        return [s["function"]["name"] for s in registry.all_schemas(**kwargs)]

    def test_additive_everything_is_both_by_default(self):
        registry = ToolRegistry()
        registry.register(self._tool("a"))
        registry.register(self._tool("b"))

        assert registry.names() == registry.names(audience="program") == ["a", "b"]
        assert self._schema_names(registry) == self._schema_names(
            registry, audience="program"
        )

    def test_folded_tools_are_marked_program(self):
        registry = ToolRegistry()
        registry.register(self._tool("orchestrator"))
        registry.register(self._tool("read", "program"))
        registry.register(self._tool("bash", "program"))

        # The model is offered the fold point only; the program side sees all.
        assert registry.names() == ["orchestrator"]
        assert registry.names(audience="program") == [
            "orchestrator",
            "read",
            "bash",
        ]

    def test_mixed_deployment_shows_each_side_its_own_half(self):
        registry = ToolRegistry()
        registry.register(self._tool("plain"))
        registry.register(self._tool("sdk_only", "program"))
        registry.register(self._tool("model_only", "model"))

        assert registry.names() == ["plain", "model_only"]
        assert registry.names(audience="program") == ["plain", "sdk_only"]
        assert self._schema_names(registry) == ["plain", "model_only"]
        assert self._schema_names(registry, audience="program") == [
            "plain",
            "sdk_only",
        ]

    def test_select_filters_by_audience_too(self):
        registry = ToolRegistry()
        registry.register(self._tool("plain"))
        registry.register(self._tool("sdk_only", "program"))

        assert registry.select().names() == ["plain"]
        child = registry.select(audience="program")
        assert child.names(audience="program") == ["plain", "sdk_only"]
        assert child.get("sdk_only") is registry.get("sdk_only")  # shared instances

    def test_freeze_pins_the_model_projection_only(self):
        registry = ToolRegistry()
        registry.register(self._tool("plain"))
        registry.register(self._tool("sdk_only", "program"))
        registry.freeze()
        registry.register(self._tool("late"))

        # The model's offered interface is held still; the program side —
        # which no request payload carries — keeps reading live.
        assert self._schema_names(registry) == ["plain"]
        assert self._schema_names(registry, audience="program") == [
            "plain",
            "sdk_only",
            "late",
        ]

    def test_an_unknown_availability_is_rejected_at_construction(self):
        with pytest.raises(ValueError, match="availability"):
            self._tool("typo", "modle")

    async def test_execution_permission_follows_the_origin(self):
        dispatcher, _, _ = _bare_dispatcher(
            self._tool("plain"),
            self._tool("sdk_only", "program"),
            self._tool("model_only", "model"),
        )
        cases = [
            ("model", "plain", "ok"),
            ("model", "model_only", "ok"),
            ("model", "sdk_only", "denied"),
            ("program", "plain", "ok"),
            ("program", "sdk_only", "ok"),
            ("program", "model_only", "denied"),
        ]
        for origin, name, expected in cases:
            result = await dispatcher.run(name, {}, origin=origin)
            assert result.status == expected, (origin, name, result.status)


# ── provenance: who asked, and nested in what ────────────────


class TestProvenance:
    async def test_events_say_who_asked_and_what_they_are_nested_in(self):
        dispatcher, events, _ = _bare_dispatcher(echo_tool())
        await dispatcher.run(
            "echo", {"value": "x"}, origin="program", parent_call_id="p1"
        )

        started, finished = events
        assert (started.origin, started.parent_call_id) == ("program", "p1")
        assert (finished.origin, finished.parent_call_id) == ("program", "p1")

    async def test_model_origin_is_the_default_and_carries_no_parent(self):
        dispatcher, events, _ = _bare_dispatcher(echo_tool())
        await dispatcher.run("echo", {"value": "x"}, call_id="c1")

        started, finished = events
        assert started.origin == "model" and started.parent_call_id is None
        assert finished.origin == "model" and finished.parent_call_id is None

    async def test_provenance_crosses_a_process_boundary_as_plain_data(self):
        dispatcher, events, _ = _bare_dispatcher(echo_tool())
        await dispatcher.run("echo", {}, origin="program", parent_call_id="p1")

        data = events[0].to_dict()
        assert data["origin"] == "program"
        assert data["parent_call_id"] == "p1"
        # A dict written before the fields existed still reads: the defaults
        # are what an old event carries.
        legacy = ToolCallStarted(call_id="c1", name="echo")
        assert (legacy.origin, legacy.parent_call_id) == ("model", None)
        legacy = ToolCallFinished(call_id="c1", name="echo")
        assert (legacy.origin, legacy.parent_call_id) == ("model", None)

    async def test_hooks_see_provenance_on_the_context(self):
        seen: list[tuple[str, str | None]] = []

        class Recorder(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                seen.append((ctx.origin, ctx.parent_call_id))

        dispatcher, _, _ = _bare_dispatcher(echo_tool(), hooks=[Recorder()])
        await dispatcher.run("echo", {}, call_id="c1")
        await dispatcher.run("echo", {}, origin="program", parent_call_id="c1")

        assert seen == [("model", None), ("program", "c1")]


# ── attribution: what ctx.emit belongs to ────────────────────


def _host_context(tmp_path, agent: AgentLoop) -> HostContext:
    return HostContext(
        home=tmp_path / "home",
        cwd=tmp_path,
        config=Config(provider="p", model="m"),
        agent=agent,
    )


class TestEmitAttribution:
    async def test_an_emit_during_a_run_belongs_to_the_turn(self, tmp_path):
        class Talker(AgentHook):
            async def before_iteration(self, ctx: IterationContext) -> None:
                await host_ctx.emit(Notice(message="mid-run"))

        agent = make_agent(provider=MockProvider([say("done")]), hooks=[Talker()])
        host_ctx = _host_context(tmp_path, agent)

        turn = agent.start("hi")
        events = await collect(turn.subscribe())

        notice = next(event for event in events if isinstance(event, Notice))
        assert notice.message == "mid-run"
        assert notice.run_id == turn.id

    async def test_an_idle_emit_has_no_run_and_no_turn_claims_it(self, tmp_path):
        agent = make_agent()
        host_ctx = _host_context(tmp_path, agent)

        reader = agent.channel.subscribe()
        await host_ctx.emit(Notice(message="idle words"))
        seen = []
        while (event := reader.take()) is not None:
            seen.append(event)
        assert [e.run_id for e in seen] == [""]

        # …and a later turn's view does not reach back for it.
        agent.provider = MockProvider([say("done")])
        turn_events = await collect(agent.stream("go"))
        assert not any(isinstance(event, Notice) for event in turn_events)

    async def test_a_publisher_may_claim_its_own_run_id(self, tmp_path):
        agent = make_agent()
        host_ctx = _host_context(tmp_path, agent)
        claimed = Notice(message="mine", run_id="someone-elses")

        await host_ctx.emit(claimed)

        assert claimed.run_id == "someone-elses"


class TestSpawn:
    """HostContext.spawn — derive() with the plugin-facing defaults fixed."""

    def _host(self, tmp_path, *tools) -> HostContext:
        agent = make_agent(*tools)
        return _host_context(tmp_path, agent)

    def test_visible_by_default_and_shares_the_parents_channel(self, tmp_path):
        host = self._host(tmp_path)

        child = host.spawn(system_prompt="focused")

        assert child.channel is host.agent.channel
        assert child.system_prompt == "focused"
        assert child.messages == []  # fresh history
        assert list(child.tool_registry.names()) == list(
            host.agent.tool_registry.names()
        )  # a live copy of the parent's set

    def test_invisible_spawn_gets_a_private_stream(self, tmp_path):
        host = self._host(tmp_path)

        child = host.spawn(system_prompt="quiet", visible=False)

        assert child.channel is not host.agent.channel

    async def test_hooks_are_not_inherited(self, tmp_path):
        seen: list[Event] = []

        class Marker(AgentHook):
            async def on_event(self, event):
                seen.append(event)

        host = self._host(tmp_path)
        host.agent.hooks.add(Marker())
        host.agent.provider.responses = [say("child ran")]

        # visible=False: the child runs on a private stream, so the only way
        # the parent's Marker could fire is through the child's own hook
        # dispatch — which must not happen.
        child = host.spawn(system_prompt="clean", visible=False)
        await child.start("hi").wait()

        assert child.state.answer == "child ran"
        assert seen == []

    def test_a_narrower_tool_set_and_a_model(self, tmp_path):
        host = self._host(tmp_path, echo_tool())
        from mocode.core.provider import ModelSpec

        child = host.spawn(
            system_prompt="s",
            tools=ToolRegistry(),
            model=ModelSpec(name="cheaper"),
        )

        assert len(child.tool_registry) == 0
        assert child.model.name == "cheaper"


# ── source: who a tool belongs to ────────────────────────────


def _sourced_tool(name: str, source: str) -> Tool:
    return Tool(name, "d", {}, lambda a: f"ran:{name}", source=source)


class _Registering(Plugin):
    """A plugin that registers one tool — however that tool presents itself."""

    def __init__(self, name: str, tool: Tool):
        self.name = name
        self._tool = tool

    def build(self, ctx) -> None:
        ctx.tools.register(self._tool)


def _host_context_for_tools() -> BuildContext:
    """A BuildContext whose registry stamps — no agent, no paths that matter."""
    return BuildContext(
        home=Path(".") / "home",
        cwd=Path("."),
        config=Config(provider="p", model="m"),
    )


class TestToolSource:
    def test_bare_core_registration_stays_unattributed(self):
        registry = ToolRegistry()
        registry.register(echo_tool("echo"))
        registry.register(echo_tool("echo"))  # unattributed vs unattributed: overwrite

        assert registry.get("echo").source == ""

    def test_a_same_source_reregistration_is_a_hot_update(self):
        registry = ToolRegistry()
        registry.register(_sourced_tool("echo", "plugin:acme"))
        registry.register(_sourced_tool("echo", "plugin:acme"))

        assert registry.get("echo").source == "plugin:acme"

    def test_different_sources_collide_loudly(self):
        registry = ToolRegistry()
        registry.register(_sourced_tool("echo", "builtin:shell"))

        with pytest.raises(ToolConflictError) as exc:
            registry.register(_sourced_tool("echo", "plugin:acme"))
        assert (exc.value.existing, exc.value.incoming) == (
            "builtin:shell",
            "plugin:acme",
        )

    def test_one_sided_attribution_still_overrides(self):
        registry = ToolRegistry()
        registry.register(_sourced_tool("echo", "plugin:acme"))
        registry.register(echo_tool("echo"))
        assert registry.get("echo").source == ""

        registry.register(_sourced_tool("echo", "plugin:acme"))
        registry.register(echo_tool("echo"))
        assert registry.get("echo").source == ""

    def test_replace_forces_the_takeover(self):
        registry = ToolRegistry()
        registry.register(_sourced_tool("echo", "builtin:shell"))

        registry.register(_sourced_tool("echo", "plugin:acme"), replace=True)

        assert registry.get("echo").source == "plugin:acme"


class TestSourceStamping:
    def test_a_plugins_registrations_carry_its_channel_not_its_claim(
        self, plugin_host
    ):
        lying = _sourced_tool("greet", "builtin:shell")  # a fake identity
        host = plugin_host(
            plugins=[_Registering("acme", lying)], sources=["plugin:acme"]
        )

        assert host.ctx.tools.get("greet").source == "plugin:acme"

    def test_a_registration_outside_any_plugin_is_the_hosts(self):
        ctx = _host_context_for_tools()

        ctx.tools.register(echo_tool("manual"))

        assert ctx.tools.get("manual").source == "host"

    def test_a_registry_passed_in_is_kept_verbatim(self):
        plain = ToolRegistry()
        ctx = BuildContext(
            home=Path(".") / "home",
            cwd=Path("."),
            config=Config(provider="p", model="m"),
            tools=plain,
        )

        ctx.tools.register(echo_tool("mine"))

        assert ctx.tools is plain
        assert plain.get("mine").source == ""

    def test_builtin_tools_get_their_builtin_identity(self, tmp_path, plugin_host):
        loaded = load_plugins(plugin_dirs=[], config=Config(provider="p", model="m"))
        host = plugin_host(plugins=loaded.plugins, sources=loaded.tool_sources)

        assert host.ctx.tools.get("bash").source == "builtin:shell"
        assert host.ctx.tools.get("read").source == "builtin:filesystem"
        assert all(
            source.startswith("builtin:")
            for source in loaded.tool_sources
        )

    def test_a_discovered_plugin_gets_its_manifest_name(self, tmp_path, plugin_host):
        write_plugin(
            tmp_path / "plugins",
            "acme",
            """
            from mocode.plugins import Plugin, Tool

            class AcmePlugin(Plugin):
                name = "acme"

                def build(self, ctx):
                    ctx.tools.register(Tool(
                        name="greet", description="g",
                        schema={"type": "object", "properties": {}},
                        func=lambda args: "hi",
                        source="builtin:shell",  # a claim the path overrides
                    ))
            """,
        )
        loaded = load_plugins(
            plugin_dirs=[tmp_path / "plugins"], config=Config(provider="p", model="m")
        )
        host = plugin_host(plugins=loaded.plugins, sources=loaded.tool_sources)

        assert "plugin:acme" in loaded.tool_sources
        assert host.ctx.tools.get("greet").source == "plugin:acme"


# ── program origin inside a real loop ────────────────────────


class TestProgramOriginInsideALoop:
    async def test_a_nested_call_is_observable_but_not_conversation(self):
        async def bridge(args, ctx):
            first = await agent.dispatcher.run(
                "echo",
                {"value": "one"},
                origin="program",
                parent_call_id=ctx.tool_call_id,
            )
            second = await agent.dispatcher.run(
                "echo",
                {"value": "two"},
                origin="program",
                parent_call_id=ctx.tool_call_id,
            )
            return f"{first.content}+{second.content}"

        agent = make_agent(echo_tool(), Tool("bridge", "b", {}, bridge, with_context=True))
        agent.provider = MockProvider([tool_call_response("bridge"), say("done")])

        events = await collect(agent.stream("hi"))

        # The nested calls are visible to the turn's readers, numbered per parent.
        started = {e.call_id: e.name for e in events if isinstance(e, ToolCallStarted)}
        assert started == {"c1": "bridge", "c1:1": "echo", "c1:2": "echo"}
        # They belong to the same run — that is why the turn's view carries them.
        assert len({e.run_id for e in events}) == 1
        # Provenance is structural: the bridge is the model's call, the echoes
        # are program calls nested in it.
        nested = [e for e in events if isinstance(e, (ToolCallStarted, ToolCallFinished)) and e.call_id.startswith("c1:")]
        assert all(e.origin == "program" and e.parent_call_id == "c1" for e in nested)
        outer = [e for e in events if isinstance(e, (ToolCallStarted, ToolCallFinished)) and e.call_id == "c1"]
        assert all(e.origin == "model" and e.parent_call_id is None for e in outer)

        # The conversation stays the model's story: one tool message, and it is
        # the bridge's own result, not the nested echoes.
        tool_messages = [m for m in agent.messages if m["role"] == "tool"]
        assert [(m["tool_call_id"], m["content"]) for m in tool_messages] == [
            ("c1", "echo:one+echo:two")
        ]
        # The live state and the run's summary count model calls only.
        assert set(agent.state.tool_calls) == {"c1"}
        assert agent.state.tool_calls_made == 1
        assert events[-1].tool_calls_made == 1

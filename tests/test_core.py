"""The kernel's building blocks: the Agent builder, Prompt, Tool, HookRunner."""

from __future__ import annotations

import pytest

from mocode.core import (
    Agent,
    AgentConfig,
    AgentHook,
    AgentLoop,
    HookRunner,
    IterationContext,
    ModelSpec,
    Prompt,
    Section,
    Tool,
    ToolCallContext,
    ToolError,
    ToolRegistry,
)

from .providers import MockProvider


class TestAgentBuilder:
    def test_minimal_build(self):
        agent = Agent().provider(MockProvider()).prompt("Hello").build()
        assert isinstance(agent, AgentLoop)
        assert agent.system_prompt == "Hello"
        assert agent.tool_registry.names() == []

    def test_provider_and_prompt_are_required(self):
        with pytest.raises(ValueError, match="Provider is required"):
            Agent().prompt("test").build()
        with pytest.raises(ValueError, match="System prompt is required"):
            Agent().provider(MockProvider()).build()

    def test_tools_accept_a_list_or_a_registry(self):
        tool = Tool("echo", "Echo", {}, lambda a: "ok")
        registry = ToolRegistry()
        registry.register(tool)

        from_list = Agent().provider(MockProvider()).prompt("t").tools([tool]).build()
        from_registry = Agent().provider(MockProvider()).prompt("t").tools(registry).build()

        assert from_list.tool_registry is not registry
        assert from_list.tool_registry.get("echo") is tool
        assert from_registry.tool_registry is registry

    def test_prompt_accepts_a_string_a_prompt_or_sections(self):
        sections = [Section("id", "bot"), Section("rules", "help")]

        from_sections = Agent().provider(MockProvider()).prompt(sections).build()
        from_prompt = (
            Agent().provider(MockProvider()).prompt(Prompt().register(Section("id", "bot"))).build()
        )

        assert "<id>" in from_sections.system_prompt
        assert "<rules>" in from_sections.system_prompt
        assert "<id>" in from_prompt.system_prompt

    def test_config_and_model_are_passed_through(self):
        spec = ModelSpec(name="m", context_window=100_000, max_output=4096)
        agent = (
            Agent()
            .provider(MockProvider())
            .prompt("t")
            .config(AgentConfig(tool_result_limit=4096))
            .model(spec)
            .build()
        )
        assert agent.config.tool_result_limit == 4096
        assert agent.model is spec

    def test_model_defaults_to_the_provider_name_with_no_invented_limits(self):
        agent = Agent().provider(MockProvider()).prompt("t").build()
        assert agent.model.name == "mock"
        assert (agent.model.context_window, agent.model.max_output) == (None, None)

    def test_hooks_are_wrapped_in_a_runner(self):
        agent = Agent().provider(MockProvider()).prompt("t").hooks([AgentHook()]).build()
        assert isinstance(agent.hooks, HookRunner)
        assert len(agent.hooks.all()) == 1


class TestPrompt:
    def test_xml_format_and_priority_order(self):
        result = (
            Prompt()
            .register(Section("z", "second", priority=20))
            .register(Section("a", "first", priority=10))
            .build()
        )
        assert "<system-prompt>" in result
        assert result.index("first") < result.index("second")

    def test_insertion_order_within_a_priority(self):
        result = (
            Prompt()
            .register(Section("first", "aaa", priority=10))
            .register(Section("second", "bbb", priority=10))
            .build()
        )
        assert result.index("aaa") < result.index("bbb")

    def test_disable_hides_a_section(self):
        prompt = Prompt().register(Section("a", "vis")).register(Section("b", "hid"))
        prompt.disable("b")
        assert "hid" not in prompt.build()

    def test_nested_sections_render_as_nested_tags(self):
        result = (
            Prompt()
            .register(
                Section(
                    "tools",
                    [
                        Section("tool", "Run bash", attrs={"name": "bash"}),
                        Section("tool", "Read files", attrs={"name": "read"}),
                    ],
                )
            )
            .build()
        )
        assert '<tool name="bash">\nRun bash\n</tool>' in result
        assert '<tool name="read">\nRead files\n</tool>' in result


class TestTool:
    def test_sync_and_async_execution(self):
        def sync(args):
            return f"sync:{args['v']}"

        async def async_(args):
            return f"async:{args['v']}"

        params = {"v": {"type": "string", "description": "v"}}
        assert Tool("s", "d", params, sync).run({"v": "x"}) == "sync:x"

        tool = Tool("a", "d", params, async_)
        assert tool.is_async is True
        assert tool.wants_context is False

    @pytest.mark.asyncio
    async def test_async_tool_runs(self):
        async def run(args):
            return f"async:{args['v']}"

        tool = Tool("a", "d", {"v": {"type": "string", "description": "v"}}, run)
        assert await tool.run_async({"v": "x"}) == "async:x"

    def test_a_tool_may_declare_a_second_parameter_for_its_context(self):
        def plain(args):
            return "plain"

        def contextual(args, ctx):
            return f"ctx:{ctx.tool_call_id}"

        params: dict = {}
        assert Tool("p", "d", params, plain).wants_context is False
        assert Tool("c", "d", params, contextual).wants_context is True

        ctx = ToolCallContext(tool_name="c", tool_call_id="call_1")
        assert Tool("c", "d", params, contextual).run({}, ctx) == "ctx:call_1"

    def test_errors_propagate_to_the_caller(self):
        def boom(args):
            raise ToolError("broke", "bad")

        with pytest.raises(ToolError):
            Tool("t", "T", {}, boom).run({})

    def test_missing_required_parameter_is_rejected(self):
        tool = Tool("t", "T", {"a": {"type": "string", "description": "a"}}, lambda a: "ok")
        with pytest.raises(ToolError, match="Missing required parameter"):
            tool.run({})

    def test_defaults_are_filled_in(self):
        tool = Tool(
            "t",
            "T",
            {"a": {"type": "string", "description": "a", "default": "fallback"}},
            lambda a: a["a"],
        )
        assert tool.run({}) == "fallback"

    def test_schema_marks_required_and_optional_parameters(self):
        schema = Tool(
            "t",
            "T",
            {
                "name": {"type": "string", "description": "name"},
                "count": {"type": "number", "description": "count", "optional": True},
            },
            lambda a: "ok",
        ).to_schema()
        params = schema["function"]["parameters"]
        assert params["required"] == ["name"]
        assert params["properties"]["count"]["type"] == "number"


class TestHookRunner:
    @pytest.mark.asyncio
    async def test_hooks_fan_out_in_order(self):
        calls = []

        class H1(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("h1")

        class H2(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("h2")

        await HookRunner([H1(), H2()]).before_iteration(IterationContext())
        assert calls == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_a_failing_hook_does_not_break_the_others(self):
        calls = []

        class Bad(AgentHook):
            async def before_iteration(self, ctx):
                raise RuntimeError("boom")

        class Good(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("good")

        await HookRunner([Bad(), Good()]).before_iteration(IterationContext())
        assert calls == ["good"]

    @pytest.mark.asyncio
    async def test_tool_hooks_see_args_and_result(self):
        seen = []

        class ToolHook(AgentHook):
            async def on_tool_start(self, ctx):
                seen.append(("start", ctx.tool_name, ctx.tool_args))

            async def on_tool_complete(self, ctx):
                seen.append(("complete", ctx.status, ctx.tool_result))

        runner = HookRunner([ToolHook()])
        ctx = ToolCallContext(tool_name="bash", tool_args={"cmd": "ls"}, tool_call_id="c1")
        await runner.on_tool_start(ctx)
        ctx.tool_result = "file1\nfile2"
        await runner.on_tool_complete(ctx)

        assert seen == [
            ("start", "bash", {"cmd": "ls"}),
            ("complete", "ok", "file1\nfile2"),
        ]

    @pytest.mark.asyncio
    async def test_a_hook_added_later_is_dispatched(self):
        calls = []
        runner = HookRunner()

        class Late(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("late")

        await runner.before_iteration(IterationContext())
        assert calls == []

        runner.add(Late())
        await runner.before_iteration(IterationContext())
        assert calls == ["late"]

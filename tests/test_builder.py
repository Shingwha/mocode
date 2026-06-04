"""Integration tests for MoCode core."""

import asyncio
import pytest

from mocode.core import (
    Agent,
    AgentLoop,
    AgentConfig,
    Tool,
    ToolRegistry,
    ToolError,
    AgentHook,
    AgentHookContext,
    HookRunner,
    Prompt,
    Section,
    Response,
    ToolCall,
)


# ---- Mock Provider ----


class MockProvider:
    def __init__(self, responses: list[Response] | None = None):
        self._responses = responses or []
        self._call_count = 0
        self._model = "mock-model"

    @property
    def model(self) -> str:
        return self._model

    async def call(self, messages, system, tools, max_tokens) -> Response:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return Response(content="done")


# ---- Builder ----


class TestBuilder:
    def test_minimal_build(self):
        agent = Agent().provider(MockProvider()).prompt("Hello").build()
        assert isinstance(agent, AgentLoop)
        assert agent.system_prompt == "Hello"

    def test_requires_provider(self):
        with pytest.raises(ValueError, match="Provider is required"):
            Agent().prompt("test").build()

    def test_requires_prompt(self):
        with pytest.raises(ValueError, match="System prompt is required"):
            Agent().provider(MockProvider()).build()

    def test_tool_list(self):
        tool = Tool(
            "echo",
            "Echo",
            {"text": {"type": "string", "description": "text"}},
            lambda a: a["text"],
        )
        agent = Agent().provider(MockProvider()).prompt("t").tools([tool]).build()
        assert agent._tools.get("echo") is not None

    def test_tool_registry(self):
        reg = ToolRegistry()
        reg.register(
            Tool(
                "add",
                "Add",
                {
                    "a": {"type": "number", "description": "a"},
                    "b": {"type": "number", "description": "b"},
                },
                lambda a: str(int(a["a"]) + int(a["b"])),
            )
        )
        agent = Agent().provider(MockProvider()).prompt("t").tools(reg).build()
        assert agent._tools.get("add") is not None

    def test_prompt_builder(self):
        prompt = (
            Prompt()
            .register(Section("id", "bot", priority=10))
            .register(Section("rules", "help", priority=20))
        )
        agent = Agent().provider(MockProvider()).prompt(prompt).build()
        assert "<id>" in agent.system_prompt
        assert "<rules>" in agent.system_prompt

    def test_prompt_section_list(self):
        agent = (
            Agent()
            .provider(MockProvider())
            .prompt(
                [
                    Section("id", "bot"),
                    Section("rules", "help"),
                ]
            )
            .build()
        )
        assert "<id>" in agent.system_prompt
        assert "<rules>" in agent.system_prompt

    def test_custom_config(self):
        agent = (
            Agent()
            .provider(MockProvider())
            .prompt("t")
            .config(AgentConfig(max_tokens=4096))
            .build()
        )
        assert agent.config.max_tokens == 4096

    def test_no_tools_valid(self):
        agent = Agent().provider(MockProvider()).prompt("t").build()
        assert agent._tools.all() == []

    def test_hooks(self):
        hook = AgentHook()
        agent = Agent().provider(MockProvider()).prompt("t").hooks([hook]).build()
        assert isinstance(agent.hooks, HookRunner)
        assert len(agent.hooks._hooks) == 1


# ---- Chat ----


class TestChat:
    @pytest.mark.asyncio
    async def test_simple_chat(self):
        agent = (
            Agent()
            .provider(MockProvider([Response(content="Hi!")]))
            .prompt("t")
            .build()
        )
        result = await agent.chat("hello")
        assert result == "Hi!"
        assert len(agent.messages) == 2

    @pytest.mark.asyncio
    async def test_tool_execution(self):
        tool = Tool(
            "add",
            "Add",
            {
                "a": {"type": "number", "description": "a"},
                "b": {"type": "number", "description": "b"},
            },
            lambda a: str(int(a["a"]) + int(a["b"])),
        )
        provider = MockProvider(
            [
                Response(
                    content="",
                    tool_calls=[
                        ToolCall(id="tc1", name="add", arguments='{"a": "3", "b": "5"}')
                    ],
                ),
                Response(content="8"),
            ]
        )
        agent = Agent().provider(provider).prompt("t").tools([tool]).build()
        result = await agent.chat("add 3 5")
        assert result == "8"
        assert len(agent.messages) == 4

    @pytest.mark.asyncio
    async def test_hooks_emitted(self):
        events = []

        class TestHook(AgentHook):
            async def after_iteration(self, ctx):
                events.append("after_iteration")

        agent = (
            Agent()
            .provider(MockProvider([Response(content="hi")]))
            .prompt("t")
            .hooks([TestHook()])
            .build()
        )
        await agent.chat("hello")
        assert "after_iteration" in events

    @pytest.mark.asyncio
    async def test_cancel_propagates(self):
        """Cancel via asyncio.Task.cancel() raises CancelledError."""

        async def delayed_call(messages, system, tools, max_tokens):
            await asyncio.sleep(10)
            return Response(content="never")

        agent = AgentLoop(
            provider=MockProvider(),
            system_prompt="t",
            tools=ToolRegistry(),
            hooks=HookRunner(),
        )
        agent.provider.call = delayed_call
        agent.messages.append({"role": "user", "content": "hello"})

        task = asyncio.create_task(agent._loop())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_parallel_tools(self):
        tools = [
            Tool("t1", "T1", {}, lambda a: "r1"),
            Tool("t2", "T2", {}, lambda a: "r2"),
        ]
        provider = MockProvider(
            [
                Response(
                    content="",
                    tool_calls=[
                        ToolCall(id="1", name="t1", arguments="{}"),
                        ToolCall(id="2", name="t2", arguments="{}"),
                    ],
                ),
                Response(content="done"),
            ]
        )
        agent = Agent().provider(provider).prompt("t").tools(tools).build()
        result = await agent.chat("run both")
        assert result == "done"

    @pytest.mark.asyncio
    async def test_clear(self):
        agent = (
            Agent().provider(MockProvider([Response(content="hi")])).prompt("t").build()
        )
        await agent.chat("hello")
        assert len(agent.messages) > 0
        agent.messages = []
        assert len(agent.messages) == 0

    @pytest.mark.asyncio
    async def test_update_prompt(self):
        agent = (
            Agent()
            .provider(MockProvider([Response(content="ok")]))
            .prompt("old")
            .build()
        )
        agent.system_prompt = "new"
        assert agent.system_prompt == "new"
        agent.system_prompt = "newer"
        agent.messages = []
        assert agent.system_prompt == "newer"
        assert len(agent.messages) == 0


# ---- Prompt ----


class TestPrompt:
    def test_xml(self):
        result = Prompt().register(Section("a", "x")).build(fmt="xml")
        assert "<system-prompt>" in result
        assert "<a>" in result

    def test_text_default(self):
        result = Prompt().register(Section("a", "x")).build()
        assert result == "a: x"

    def test_text_attrs(self):
        result = (
            Prompt()
            .register(Section("tool", "desc", attrs={"name": "bash", "type": "shell"}))
            .build()
        )
        assert "tool (name=bash, type=shell): desc" == result

    def test_priority(self):
        result = (
            Prompt()
            .register(Section("z", "second", priority=20))
            .register(Section("a", "first", priority=10))
            .build(fmt="text")
        )
        assert result.index("first") < result.index("second")

    def test_disable(self):
        p = Prompt().register(Section("a", "vis")).register(Section("b", "hid"))
        p.disable("b")
        assert "hid" not in p.build(fmt="text")

    def test_context(self):
        p = Prompt().register(Section("g", lambda c: f"hi {c.get('name', 'world')}"))
        assert "hi MoCode" in p.context(name="MoCode").build(fmt="text")

    def test_find(self):
        p = Prompt().register(Section("x", "content"))
        assert p.get("x") is not None
        assert p.get("x").content == "content"
        assert p.get("missing") is None

    def test_all(self):
        p = Prompt().register(Section("a", "1")).register(Section("b", "2"))
        names = {s.name for s in p.all()}
        assert names == {"a", "b"}

    def test_remove(self):
        p = Prompt().register(Section("x", "content"))
        removed = p.unregister("x")
        assert removed is not None
        assert removed.name == "x"
        assert p.get("x") is None
        assert p.unregister("missing") is None

    def test_add_replaces_duplicate(self):
        p = Prompt().register(Section("x", "old")).register(Section("x", "new"))
        assert p.get("x").content == "new"
        assert len(p.all()) == 1

    def test_repr(self):
        p = Prompt().register(Section("a", "1")).register(Section("b", "2"))
        p.disable("b")
        r = repr(p)
        assert "a(on)" in r
        assert "b(off)" in r

    def test_static_content_no_lambda(self):
        result = Prompt().register(Section("id", "You are a bot.")).build(fmt="xml")
        assert "You are a bot." in result

    def test_enable(self):
        p = Prompt().register(Section("a", "vis"))
        p.disable("a")
        assert "vis" not in p.build(fmt="text")
        p.enable("a")
        assert "vis" in p.build(fmt="text")

    def test_init_with_sections(self):
        p = Prompt([Section("a", "1"), Section("b", "2")])
        assert len(p.all()) == 2
        assert p.get("a").content == "1"

    def test_nested_section_xml(self):
        tools = Section(
            "tools",
            [
                Section("tool", "Run bash", attrs={"name": "bash"}),
                Section("tool", "Read files", attrs={"name": "read"}),
            ],
        )
        result = Prompt().register(tools).build(fmt="xml")
        assert "<tools>" in result
        assert '<tool name="bash">' in result
        assert "Run bash" in result
        assert '<tool name="read">' in result
        assert "Read files" in result

    def test_nested_section_text(self):
        tools = Section(
            "tools",
            [
                Section("tool", "Run bash"),
                Section("tool", "Read files"),
            ],
        )
        result = Prompt().register(tools).build(fmt="text")
        assert "Run bash" in result
        assert "Read files" in result
        assert "<tools>" not in result
        assert "<tool>" not in result

    def test_nested_disabled_child_skipped(self):
        tools = Section(
            "tools",
            [
                Section("tool", "visible"),
                Section("tool", "hidden", enabled=False),
            ],
        )
        result = Prompt().register(tools).build(fmt="xml")
        assert "visible" in result
        assert "hidden" not in result

    def test_deep_nested(self):
        inner = Section(
            "tools",
            [
                Section(
                    "tool",
                    [
                        Section("param", "verbose", attrs={"name": "v"}),
                    ],
                    attrs={"name": "bash"},
                ),
            ],
        )
        result = Prompt().register(inner).build(fmt="xml")
        assert "<tools>" in result
        assert '<tool name="bash">' in result
        assert '<param name="v">' in result
        assert "verbose" in result


# ---- Tool ----


class TestTool:
    def test_sync(self):
        assert (
            Tool(
                "add",
                "Add",
                {
                    "a": {"type": "string", "description": "a"},
                    "b": {"type": "string", "description": "b"},
                },
                lambda a: str(int(a["a"]) + int(a["b"])),
            ).run({"a": "3", "b": "5"})
            == "8"
        )

    @pytest.mark.asyncio
    async def test_async(self):
        async def f(a):
            return f"async:{a['x']}"

        assert (
            await Tool(
                "t", "T", {"x": {"type": "string", "description": "x"}}, f
            ).run_async({"x": "hi"})
            == "async:hi"
        )

    def test_error_propagates(self):
        """ToolError propagates — caller handles it."""

        def f(a):
            raise ToolError("broke", "bad")

        with pytest.raises(ToolError):
            Tool("t", "T", {}, f).run({})

    def test_schema(self):
        schema = Tool(
            "t",
            "T",
            {
                "name": {"type": "string", "description": "name"},
                "count": {"type": "number", "description": "count", "optional": True},
            },
            lambda a: "ok",
        ).to_schema()
        assert "name" in schema["function"]["parameters"]["required"]
        assert "count" not in schema["function"]["parameters"]["required"]

    def test_derived(self):
        reg = ToolRegistry()
        reg.register(Tool("a", "A", {}, lambda a: "a"))
        reg.register(Tool("b", "B", {}, lambda a: "b"))
        d = reg.derived(exclude={"b"})
        assert d.get("a") is not None
        assert d.get("b") is None
        assert reg.get("b") is not None


# ---- AgentHook / HookRunner ----


class TestAgentHook:
    @pytest.mark.asyncio
    async def test_hook_receives_ctx(self):
        received = []

        class TestHook(AgentHook):
            async def after_iteration(self, ctx):
                received.append(ctx)

        ctx = AgentHookContext(final_content="test")
        runner = HookRunner([TestHook()])
        await runner.after_iteration(ctx)
        assert len(received) == 1
        assert received[0].final_content == "test"

    @pytest.mark.asyncio
    async def test_multiple_hooks_fan_out(self):
        calls = []

        class H1(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("h1")

        class H2(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("h2")

        runner = HookRunner([H1(), H2()])
        await runner.before_iteration(AgentHookContext())
        assert calls == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        class BadHook(AgentHook):
            async def before_iteration(self, ctx):
                raise RuntimeError("boom")

        calls = []

        class GoodHook(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("good")

        runner = HookRunner([BadHook(), GoodHook()])
        await runner.before_iteration(AgentHookContext())
        assert calls == ["good"]

    @pytest.mark.asyncio
    async def test_before_iteration_modifies_messages(self):
        class FilterHook(AgentHook):
            async def before_iteration(self, ctx):
                ctx.messages[:] = [m for m in ctx.messages if m.get("role") != "system"]

        runner = HookRunner([FilterHook()])
        ctx = AgentHookContext(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
        )
        await runner.before_iteration(ctx)
        assert len(ctx.messages) == 1
        assert ctx.messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_add_hook(self):
        runner = HookRunner()
        assert len(runner._hooks) == 0
        runner.add(AgentHook())
        assert len(runner._hooks) == 1

    @pytest.mark.asyncio
    async def test_tool_hooks(self):
        events = []

        class ToolHook(AgentHook):
            async def on_tool_start(self, ctx):
                events.append(("start", ctx.tool_name))

            async def on_tool_complete(self, ctx):
                events.append(("complete", ctx.tool_name, ctx.tool_result))

        runner = HookRunner([ToolHook()])
        ctx = AgentHookContext(
            tool_name="bash", tool_args={"cmd": "ls"}, tool_call_id="c1"
        )
        await runner.on_tool_start(ctx)
        ctx.tool_result = "file1\nfile2"
        await runner.on_tool_complete(ctx)
        assert events == [("start", "bash"), ("complete", "bash", "file1\nfile2")]

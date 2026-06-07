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
    def test_xml_format(self):
        result = Prompt().register(Section("a", "x")).build(fmt="xml")
        assert "<system-prompt>" in result
        assert "<a>" in result

    def test_priority_ordering(self):
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

    def test_to_schema_includes_default(self):
        schema = Tool(
            "x",
            "desc",
            {
                "p": {"type": "string", "description": "p", "default": "hello"},
                "q": {"type": "integer", "description": "q"},
            },
            lambda a: "ok",
        ).to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert props["p"]["default"] == "hello"
        assert "default" not in props["q"]


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


# ---------------------------------------------------------------------------
# AgentLoop properties
# ---------------------------------------------------------------------------


class TestAgentLoopProperties:
    def test_tool_registry_property(self):
        """AgentLoop.tool_registry is the public accessor for the internal registry."""
        reg = ToolRegistry()
        reg.register(Tool("test", "desc", {}, lambda a: "ok"))
        agent = AgentLoop(
            provider=MockProvider(), system_prompt="test",
            tools=reg, hooks=HookRunner(), config=AgentConfig(),
        )
        assert agent.tool_registry is reg
        assert agent.tool_registry.get("test") is not None

    def test_iteration_property(self):
        """AgentLoop.iteration starts at 0 and is exposed."""
        agent = AgentLoop(
            provider=MockProvider(), system_prompt="test",
            tools=ToolRegistry(), hooks=HookRunner(), config=AgentConfig(),
        )
        assert agent.iteration == 0

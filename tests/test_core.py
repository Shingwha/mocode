"""The kernel's building blocks: AgentLoop assembly, Prompt, Tool, HookRunner."""

from __future__ import annotations

import pytest

from mocode.core import (
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
from mocode.testing import MockProvider


def _loop(**kwargs) -> AgentLoop:
    """An AgentLoop with the obvious defaults filled in."""
    kwargs.setdefault("provider", MockProvider())
    kwargs.setdefault("system_prompt", "t")
    kwargs.setdefault("tools", ToolRegistry())
    kwargs.setdefault("hooks", HookRunner())
    return AgentLoop(**kwargs)


class TestAgentLoopAssembly:
    def test_minimal_construction(self):
        agent = _loop(system_prompt="Hello")
        assert isinstance(agent, AgentLoop)
        assert agent.system_prompt == "Hello"
        assert agent.tool_registry.names() == []

    def test_config_and_model_are_passed_through(self):
        spec = ModelSpec(name="m", context_window=100_000, max_tokens=4096)
        agent = _loop(config=AgentConfig(tool_result_limit=4096), model=spec)
        assert agent.config.tool_result_limit == 4096
        assert agent.model is spec

    def test_model_defaults_to_the_provider_name_with_no_invented_limits(self):
        agent = _loop()
        assert agent.model.name == "mock"
        assert (agent.model.context_window, agent.model.max_tokens) == (None, None)

    def test_two_loops_never_share_a_channel(self):
        a, b = _loop(), _loop()
        assert a.channel is not b.channel

    def test_state_is_stable_before_the_first_turn(self):
        agent = _loop()
        assert agent.state is agent.state
        assert agent.state.status == "idle"


class TestContainerSurface:
    """ToolRegistry and Prompt are the same shape of container.

    ``all()`` is the management view; ``names()`` is the visible projection
    (enabled only); ``in`` / ``len()`` count what is registered, enabled or not.
    """

    def test_tool_registry_management(self):
        registry = ToolRegistry()
        tool = Tool("t", "d", {}, lambda a: "ok")
        assert registry.register(tool) is registry

        assert len(registry) == 1
        assert "t" in registry
        assert registry.get("t") is tool
        assert registry.all() == [tool]
        assert registry.names() == ["t"]

        registry.disable("t")
        assert "t" in registry  # still registered, just not offered
        assert registry.all_schemas() == []
        assert registry.names() == []
        assert registry.select().names() == []

        registry.enable("t")
        assert registry.names() == ["t"]

        assert registry.unregister("t") is tool
        assert len(registry) == 0 and "t" not in registry

    def test_prompt_management(self):
        prompt = Prompt().register(Section("a", "alpha")).register(Section("b", "beta"))
        assert len(prompt) == 2
        assert "a" in prompt
        assert prompt.names() == ["a", "b"]

        prompt.disable("b")
        assert "b" in prompt  # still registered, just not rendered
        assert prompt.names() == ["a"]
        assert "beta" not in prompt.build()

        prompt.enable("b")
        assert "beta" in prompt.build()

        assert prompt.unregister("b").name == "b"
        assert len(prompt) == 1

    def test_re_registering_a_tool_clears_its_disabled_state(self):
        registry = ToolRegistry()
        tool = Tool("t", "d", {}, lambda a: "ok")
        registry.register(tool).disable("t").register(tool)
        assert registry.names() == ["t"]


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

    def test_a_disabled_section_is_hidden(self):
        prompt = (
            Prompt()
            .register(Section("a", "vis"))
            .register(Section("b", "hid", enabled=False))
        )
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

    def test_a_render_field_produces_the_text(self):
        section = Section("sdk", render=lambda ctx: f"tools known: {ctx.get('n', 0)}")

        result = Prompt().register(section).build()

        assert "tools known: 0" in result

    def test_render_beats_a_callable_content(self):
        section = Section(
            "both",
            lambda _ctx: "from content",
            render=lambda _ctx: "from render",
        )

        assert "from render" in Prompt().register(section).build()

    def test_a_pinned_section_renders_once_and_holds_its_bytes(self):
        state = {"n": 1}
        section = Section("sdk", render=lambda _ctx: f"v={state['n']}", pinned=True)
        prompt = Prompt().register(section)

        first = prompt.build()
        state["n"] = 2  # the live source moved
        second = prompt.build()

        assert first == second  # the pin kept the prompt byte-identical
        assert "v=1" in second

        section.refresh()
        third = prompt.build()

        assert "v=2" in third
        assert third != first

    def test_prompt_render_reads_a_section_live_even_when_pinned(self):
        state = {"n": 1}
        section = Section("sdk", render=lambda _ctx: f"v={state['n']}", pinned=True)
        prompt = Prompt().register(section)

        prompt.build()  # the pin caches v=1
        state["n"] = 2

        assert "v=2" in prompt.render(section)

    def test_derived_from_is_carried_not_interpreted(self):
        section = Section("sdk", "text", derived_from="tools")

        assert section.derived_from == "tools"
        assert "text" in Prompt().register(section).build()


class TestTool:
    def test_sync_and_async_execution(self):
        def sync(args):
            return f"sync:{args['v']}"

        async def async_(args):
            return f"async:{args['v']}"

        schema = {
            "type": "object",
            "properties": {"v": {"type": "string", "description": "v"}},
            "required": ["v"],
        }
        assert Tool("s", "d", schema, sync).run({"v": "x"}) == "sync:x"

        tool = Tool("a", "d", schema, async_)
        assert tool.is_async is True
        assert tool.wants_context is False

    async def test_async_tool_runs(self):
        async def run(args):
            return f"async:{args['v']}"

        tool = Tool(
            "a",
            "d",
            {
                "type": "object",
                "properties": {"v": {"type": "string", "description": "v"}},
                "required": ["v"],
            },
            run,
        )
        assert await tool.run_async({"v": "x"}) == "async:x"

    async def test_a_sync_tool_runs_through_run_async_too(self):
        def run(args):
            return f"sync:{args['v']}"

        tool = Tool(
            "s",
            "d",
            {
                "type": "object",
                "properties": {"v": {"type": "string", "description": "v"}},
                "required": ["v"],
            },
            run,
        )
        assert await tool.run_async({"v": "x"}) == "sync:x"

    def test_a_tool_declares_its_context_explicitly(self):
        def plain(args):
            return "plain"

        def contextual(args, ctx):
            return f"ctx:{ctx.tool_call_id}"

        schema: dict = {"type": "object", "properties": {}}
        assert Tool("p", "d", schema, plain).wants_context is False
        tool = Tool("c", "d", schema, contextual, with_context=True)
        assert tool.wants_context is True

        ctx = ToolCallContext(tool_name="c", tool_call_id="call_1")
        assert tool.run({}, ctx) == "ctx:call_1"

    def test_a_defaulted_second_parameter_counts_as_the_context(self):
        """The (args, ctx=None) blind spot of signature probing is legal now —
        the declaration says what the second parameter is, not its default."""

        def contextual(args, ctx=None):
            return "ctx" if ctx is not None else "bare"

        tool = Tool(
            "c", "d", {}, contextual, with_context=True
        )
        assert tool.run({}) == "bare"
        assert tool.run({}, ToolCallContext()) == "ctx"

    def test_declaring_a_context_the_function_cannot_take_fails_at_construction(self):
        def plain(args):
            return "plain"

        with pytest.raises(TypeError, match="with_context=True needs"):
            Tool("p", "d", {}, plain, with_context=True)

    def test_a_second_required_parameter_without_the_declaration_fails_at_construction(self):
        def forgot(args, ctx):
            return "never runs"

        with pytest.raises(TypeError, match="with_context=True"):
            Tool("f", "d", {}, forgot)

    def test_errors_propagate_to_the_caller(self):
        def boom(args):
            raise ToolError("broke", "bad")

        with pytest.raises(ToolError):
            Tool("t", "T", {}, boom).run({})

    def test_missing_required_parameter_is_rejected(self):
        tool = Tool(
            "t",
            "T",
            {
                "type": "object",
                "properties": {"a": {"type": "string", "description": "a"}},
                "required": ["a"],
            },
            lambda a: "ok",
        )
        with pytest.raises(ToolError, match="missing required"):
            tool.run({})

    def test_defaults_are_filled_in(self):
        tool = Tool(
            "t",
            "T",
            {
                "type": "object",
                "properties": {
                    "a": {"type": "string", "description": "a", "default": "fallback"}
                },
            },
            lambda a: a["a"],
        )
        assert tool.run({}) == "fallback"


# ── the schema dialect and its checker ──────────────────────


def _tool_with(schema: dict) -> Tool:
    return Tool("t", "T", schema, lambda a: "ok")


class TestSchemaDeclaration:
    def test_to_schema_passes_the_object_node_through(self):
        schema = {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {"field": {"type": "string"}},
                    "required": ["field"],
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["filter"],
        }

        assert _tool_with(schema).to_schema()["function"]["parameters"] is schema

    def test_a_non_dict_schema_is_refused_at_construction(self):
        with pytest.raises(TypeError, match="JSON Schema object node"):
            Tool("t", "T", ["not", "a", "schema"], lambda a: "ok")

    def test_summary_key_defaults_to_the_first_required_parameter(self):
        schema = {
            "type": "object",
            "properties": {"z": {"type": "string"}, "a": {"type": "string"}},
            "required": ["z", "a"],
        }
        assert _tool_with(schema).summary_key == "z"

    def test_summary_key_falls_back_to_the_first_property(self):
        schema = {"type": "object", "properties": {"z": {"type": "string"}}}
        assert _tool_with(schema).summary_key == "z"


class TestSchemaChecker:
    """The dependency-free checker behind ``Tool._validate_args``."""

    @pytest.mark.parametrize(
        "declared,value",
        [
            ("string", "x"),
            ("integer", 3),
            ("number", 3.5),
            ("number", 3),
            ("boolean", True),
            ("array", [1, 2]),
            ("object", {"a": 1}),
            ("null", None),
        ],
    )
    def test_values_of_the_declared_type_pass(self, declared, value):
        tool = _tool_with(
            {"type": "object", "properties": {"v": {"type": declared}}}
        )
        assert tool.run({"v": value}) == "ok"

    @pytest.mark.parametrize(
        "declared,value",
        [
            ("string", 3),
            ("integer", "3"),
            ("integer", 3.5),
            ("number", "3"),
            ("boolean", "yes"),
            ("array", {"a": 1}),
            ("object", [1]),
            ("null", 0),
        ],
    )
    def test_values_of_another_type_are_rejected(self, declared, value):
        tool = _tool_with(
            {"type": "object", "properties": {"v": {"type": declared}}}
        )
        with pytest.raises(ToolError) as exc:
            tool.run({"v": value})
        assert exc.value.code == "invalid_type"

    @pytest.mark.parametrize("declared", ["integer", "number"])
    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_not_a_number(self, declared, value):
        """Python bools are ints; JSON booleans are not numbers."""
        tool = _tool_with(
            {"type": "object", "properties": {"v": {"type": declared}}}
        )
        with pytest.raises(ToolError) as exc:
            tool.run({"v": value})
        assert exc.value.code == "invalid_type"

    def test_a_missing_required_property_is_missing_param(self):
        tool = _tool_with(
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
            }
        )
        with pytest.raises(ToolError) as exc:
            tool.run({})
        assert exc.value.code == "missing_param"

    def test_enum_rejects_values_outside_it(self):
        tool = _tool_with(
            {
                "type": "object",
                "properties": {"state": {"enum": ["open", "closed"]}},
            }
        )
        assert tool.run({"state": "open"}) == "ok"
        with pytest.raises(ToolError) as exc:
            tool.run({"state": "wedged"})
        assert exc.value.code == "invalid_type"

    def test_any_of_accepts_any_branch(self):
        tool = _tool_with(
            {
                "type": "object",
                "properties": {
                    "v": {"anyOf": [{"type": "string"}, {"type": "integer"}]}
                },
            }
        )
        assert tool.run({"v": "x"}) == "ok"
        assert tool.run({"v": 1}) == "ok"
        with pytest.raises(ToolError) as exc:
            tool.run({"v": True})
        assert exc.value.code == "invalid_type"

    def test_one_of_rejects_a_value_matching_two_branches(self):
        tool = _tool_with(
            {
                "type": "object",
                "properties": {
                    "v": {"oneOf": [{"type": "integer"}, {"type": "number"}]}
                },
            }
        )
        with pytest.raises(ToolError) as exc:  # an integer is both — ambiguous
            tool.run({"v": 1})
        assert exc.value.code == "invalid_type"
        with pytest.raises(ToolError) as none:  # a string is neither
            tool.run({"v": "s"})
        assert none.value.code == "invalid_type"

    def test_nested_objects_validate_recursively(self):
        tool = _tool_with(
            {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                        "required": ["limit"],
                    }
                },
                "required": ["filter"],
            }
        )
        assert tool.run({"filter": {"limit": 5}}) == "ok"
        with pytest.raises(ToolError) as missing:
            tool.run({"filter": {}})
        assert missing.value.code == "missing_param"
        with pytest.raises(ToolError) as bad_type:
            tool.run({"filter": {"limit": "5"}})
        assert bad_type.value.code == "invalid_type"

    def test_array_items_validate_element_wise(self):
        tool = _tool_with(
            {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            }
        )
        assert tool.run({"tags": ["a", "b"]}) == "ok"
        with pytest.raises(ToolError) as exc:
            tool.run({"tags": ["a", 2]})
        assert exc.value.code == "invalid_type"

    def test_defaults_are_filled_at_nested_levels(self):
        seen = {}

        def run(args):
            seen.update(args)
            return "ok"

        Tool(
            "t",
            "T",
            {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "default": 50},
                            "state": {"enum": ["open", "closed"], "default": "open"},
                        },
                    },
                    "page": {"type": "integer", "default": 1},
                },
            },
            run,
        ).run({"filter": {}})

        assert seen == {"filter": {"limit": 50, "state": "open"}, "page": 1}

    def test_unknown_keywords_pass(self):
        tool = _tool_with(
            {
                "type": "object",
                "properties": {
                    "v": {"type": "string", "minLength": 2, "pattern": "^x"},
                },
                "additionalProperties": False,
                "x-vendor-extension": {"anything": True},
            }
        )
        assert tool.run({"v": "whatever", "extra": 1}) == "ok"

    def test_an_empty_schema_accepts_anything(self):
        assert _tool_with({}).run({"anything": ["at", "all"]}) == "ok"


class TestHookRunner:
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

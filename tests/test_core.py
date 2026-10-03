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

from .conftest import make_agent


class TestAgentLoopAssembly:
    def test_the_constructor_takes_what_it_is_given(self):
        agent = make_agent(system_prompt="Hello")
        assert isinstance(agent, AgentLoop)
        assert agent.system_prompt == "Hello"
        assert agent.tool_registry.names() == []

        # 配置与模型原样穿过构造：一个字段、一个实例，都不被改写。
        spec = ModelSpec(name="m", context_window=100_000, max_tokens=4096)
        configured = make_agent(config=AgentConfig(tool_result_limit=4096), model=spec)
        assert configured.config.tool_result_limit == 4096
        assert configured.model is spec

        # 没给模型时用 provider 的名字，不发明上下文窗口这类限额。
        assert agent.model.name == "mock"
        assert (agent.model.context_window, agent.model.max_tokens) == (None, None)

        # 首轮之前 state 已成形且稳定；两个 loop 互不共享频道。
        assert agent.state is agent.state
        assert agent.state.status == "idle"
        other = make_agent()
        assert other.channel is not agent.channel


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

        # 同一实例再次登记：禁用态随之清除（重复登记的同一条路径）。
        registry.register(tool)
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

    def test_select_filters_by_tag_and_shares_instances(self):
        registry = ToolRegistry()
        registry.register(Tool("a", "d", {}, lambda x: "a", tags={"fs"}))
        registry.register(Tool("b", "d", {}, lambda x: "b", tags={"shell"}))
        registry.register(Tool("c", "d", {}, lambda x: "c"))

        assert registry.select(include_tags={"fs"}).names() == ["a"]
        assert registry.select(exclude_tags={"fs"}).names() == ["b", "c"]
        assert registry.select(exclude_names={"c"}).names() == ["a", "b"]
        # 过滤出来的是同一个实例，不是拷贝。
        assert registry.select().get("a") is registry.get("a")


class TestPrompt:
    def test_build_renders_the_prompt_tree(self):
        result = (
            Prompt()
            .register(Section("z", "second", priority=20))
            .register(Section("a", "first", priority=10))
            .register(Section("tie", "tied", priority=10))
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
        assert "<system-prompt>" in result
        # 同级按注册先后：first 先于 tie，两者都先于低优先级的 second。
        assert result.index("first") < result.index("tie") < result.index("second")
        # 嵌套 section 渲染成嵌套标签，属性落在开标签上。
        assert '<tool name="bash">\nRun bash\n</tool>' in result
        assert '<tool name="read">\nRead files\n</tool>' in result

        # section 自己的旋钮：render 压过 content 并从上下文读数……
        knob = Section(
            "both",
            lambda _ctx: "from content",
            render=lambda ctx: f"from render: n={ctx.get('n', 0)}",
        )
        knob_result = Prompt().register(knob).build()
        assert "from render: n=0" in knob_result
        assert "from content" not in knob_result

        # ……derived_from 原样携带，渲染层不解释它。
        carried = Section("sdk", "text", derived_from="tools")
        assert carried.derived_from == "tools"
        assert "text" in Prompt().register(carried).build()

    def test_a_pinned_section_holds_its_bytes_until_refreshed(self):
        state = {"n": 1}
        section = Section("sdk", render=lambda _ctx: f"v={state['n']}", pinned=True)
        prompt = Prompt().register(section)

        first = prompt.build()
        state["n"] = 2  # the live source moved
        second = prompt.build()

        assert first == second  # the pin kept the prompt byte-identical
        assert "v=1" in second

        # render(section) 读活的源，不受 pin 缓存的约束。
        assert "v=2" in prompt.render(section)

        section.refresh()
        third = prompt.build()

        assert "v=2" in third
        assert third != first


class TestTool:
    async def test_sync_and_async_execution(self):
        def sync(args):
            return f"sync:{args['v']}"

        async def async_(args):
            return f"async:{args['v']}"

        schema = {
            "type": "object",
            "properties": {"v": {"type": "string", "description": "v"}},
            "required": ["v"],
        }
        # run() 与 run_async() 是同一条执行路径：同步函数经 await 也一样跑。
        assert Tool("s", "d", schema, sync).run({"v": "x"}) == "sync:x"
        assert await Tool("s", "d", schema, sync).run_async({"v": "x"}) == "sync:x"

        tool = Tool("a", "d", schema, async_)
        assert tool.is_async is True
        assert tool.wants_context is False
        assert await tool.run_async({"v": "x"}) == "async:x"

    def test_metadata_declares_capability_and_the_summary_field(self):
        tool = Tool("echo", "d", {}, lambda a: "ok", tags={"fs", "demo"}, summary_key="value")
        assert tool.tags == frozenset({"fs", "demo"})
        assert tool.summary_key == "value"

        # 不给 summary_key 时按"第一个 required，否则第一个 property"推导。
        with_required = Tool(
            "t",
            "d",
            {
                "type": "object",
                "properties": {"z": {"type": "string"}, "a": {"type": "string"}},
                "required": ["z", "a"],
            },
            lambda a: "ok",
        )
        with_property_only = Tool(
            "t", "d", {"type": "object", "properties": {"z": {"type": "string"}}}, lambda a: "ok"
        )
        assert with_required.summary_key == "z"
        assert with_property_only.summary_key == "z"

    def test_the_context_declaration_is_the_contract(self):
        """第二个参数是不是上下文由声明说话，不由签名探测猜；

        声明与签名互相矛盾、或声明了未知取值，构造期就失败。
        """

        def plain(args):
            return "plain"

        def contextual(args, ctx):
            return f"ctx:{ctx.tool_call_id}"

        def defaulted(args, ctx=None):
            return "ctx" if ctx is not None else "bare"

        schema: dict = {"type": "object", "properties": {}}
        assert Tool("p", "d", schema, plain).wants_context is False
        tool = Tool("c", "d", schema, contextual, with_context=True)
        assert tool.wants_context is True
        assert (
            tool.run({}, ToolCallContext(tool_name="c", tool_call_id="call_1"))
            == "ctx:call_1"
        )
        # (args, ctx=None) 这个签名探测的盲区现在合法：声明说了算。
        blind = Tool("c", "d", {}, defaulted, with_context=True)
        assert blind.run({}) == "bare"
        assert blind.run({}, ToolCallContext()) == "ctx"

        def forgot(args, ctx):
            return "never runs"

        # 声明了 with_context，函数却接不住第二个参数。
        with pytest.raises(TypeError, match="with_context=True needs"):
            Tool("p", "d", {}, plain, with_context=True)

        # 函数接得住，却没声明——签名探测答不上来它是不是上下文。
        with pytest.raises(TypeError, match="with_context=True"):
            Tool("f", "d", {}, forgot)

        # 未知 audience 同属构造期拒绝（自 test_dispatch 的可用性矩阵合来）。
        with pytest.raises(ValueError, match="availability"):
            Tool("typo", "d", {}, lambda a: "x", availability="modle")


# ── the schema dialect and its checker ──────────────────────


def _tool_with(schema: dict) -> Tool:
    return Tool("t", "T", schema, lambda a: "ok")


class TestSchemaDeclaration:
    def test_the_schema_node_is_the_contract(self):
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

        # 声明的对象节点原样进函数描述：同一实例，不复制不改写。
        assert _tool_with(schema).to_schema()["function"]["parameters"] is schema

        # 非 dict 的 schema 构造期就拒。
        with pytest.raises(TypeError, match="JSON Schema object node"):
            Tool("t", "T", ["not", "a", "schema"], lambda a: "ok")


class TestSchemaChecker:
    """The dependency-free checker behind ``Tool._validate_args``."""

    @pytest.mark.parametrize(
        "declared,value",
        [
            ("string", "x"),
            ("integer", 3),
            ("number", 3.5),
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
        "declared,values",
        [
            ("string", (3,)),
            # Python 的 bool 是 int 子类，JSON 的布尔不是数——整型与数字
            # 两类都拒，与该类型的普通错值并入同一行，回到每类型一行。
            ("integer", (3.5, True)),
            ("number", ("3", True)),
            ("boolean", ("yes",)),
            ("array", ({"a": 1},)),
            ("object", ([1],)),
            ("null", (0,)),
        ],
    )
    def test_values_of_another_type_are_rejected(self, declared, values):
        tool = _tool_with(
            {"type": "object", "properties": {"v": {"type": declared}}}
        )
        for value in values:
            with pytest.raises(ToolError) as exc:
                tool.run({"v": value})
            assert exc.value.code == "invalid_type"

    def test_the_checker_walks_the_whole_object_node(self):
        """一遍遍历：根层与嵌套的 required、嵌套类型、数组成员、默认值。"""
        # 带 required 的递归 schema：缺参数报 missing_param，类型错报 invalid_type。
        strict = _tool_with(
            {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                        "required": ["limit"],
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["filter"],
            }
        )
        assert strict.run({"filter": {"limit": 5}, "tags": ["a", "b"]}) == "ok"

        # 缺 required：根层与嵌套层都报 missing_param。
        with pytest.raises(ToolError) as missing:
            strict.run({})
        assert missing.value.code == "missing_param"
        with pytest.raises(ToolError) as nested_missing:
            strict.run({"filter": {}})
        assert nested_missing.value.code == "missing_param"

        # 类型错：嵌套属性与数组成员都报 invalid_type。
        with pytest.raises(ToolError) as bad_type:
            strict.run({"filter": {"limit": "5"}})
        assert bad_type.value.code == "invalid_type"
        with pytest.raises(ToolError) as bad_item:
            strict.run({"filter": {"limit": 1}, "tags": ["a", 2]})
        assert bad_item.value.code == "invalid_type"

        # 带默认值的 schema：默认值在对象层级逐层填充，enumerated 的不填错。
        seen: dict = {}

        def record(args):
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
            record,
        ).run({"filter": {}})
        assert seen == {"filter": {"limit": 50, "state": "open"}, "page": 1}

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

    def test_combinators_accept_any_branch_and_reject_ambiguity(self):
        """anyOf 命中任一分支即过；oneOf 命中两个分支算歧义。"""
        any_of = _tool_with(
            {
                "type": "object",
                "properties": {
                    "v": {"anyOf": [{"type": "string"}, {"type": "integer"}]}
                },
            }
        )
        assert any_of.run({"v": "x"}) == "ok"
        assert any_of.run({"v": 1}) == "ok"
        with pytest.raises(ToolError) as exc:
            any_of.run({"v": True})
        assert exc.value.code == "invalid_type"

        one_of = _tool_with(
            {
                "type": "object",
                "properties": {
                    "v": {"oneOf": [{"type": "integer"}, {"type": "number"}]}
                },
            }
        )
        with pytest.raises(ToolError) as both:  # an integer is both — ambiguous
            one_of.run({"v": 1})
        assert both.value.code == "invalid_type"
        with pytest.raises(ToolError) as neither:  # a string is neither
            one_of.run({"v": "s"})
        assert neither.value.code == "invalid_type"

    def test_unknown_keywords_and_an_empty_schema_both_pass(self):
        """不认识的 keyword 与空 schema 都不设防——向前兼容胜过误拒。"""
        open_tool = _tool_with(
            {
                "type": "object",
                "properties": {
                    "v": {"type": "string", "minLength": 2, "pattern": "^x"},
                },
                "additionalProperties": False,
                "x-vendor-extension": {"anything": True},
            }
        )
        assert open_tool.run({"v": "whatever", "extra": 1}) == "ok"

        assert _tool_with({}).run({"anything": ["at", "all"]}) == "ok"


class TestHookRunner:
    async def test_hooks_fan_out_in_order_isolate_failures_and_accept_late_ones(self):
        calls = []

        class H1(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("h1")

        class Bad(AgentHook):
            async def before_iteration(self, ctx):
                raise RuntimeError("boom")

        class H2(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("h2")

        runner = HookRunner([H1(), Bad(), H2()])
        await runner.before_iteration(IterationContext())
        assert calls == ["h1", "h2"]  # 顺序不变，出错的钩子不连坐他人

        class Late(AgentHook):
            async def before_iteration(self, ctx):
                calls.append("late")

        runner.add(Late())
        await runner.before_iteration(IterationContext())
        # 第二次派发：原班钩子依序再来一遍，末尾才是新加入的那个。
        assert calls == ["h1", "h2", "h1", "h2", "late"]

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

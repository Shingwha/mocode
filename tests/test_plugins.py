"""Plugin system tests - HookRegistry, PluginRegistry, decorators"""

import pytest

from mocode.plugins.base import (
    Hook,
    HookContext,
    HookPoint,
    HookBase,
    Plugin,
    PluginInfo,
    PluginMetadata,
    PluginState,
)
from mocode.plugins.decorators import HookBuilder, hook, async_hook
from mocode.plugins.registry import HookRegistry, PluginRegistry


# --- Test hooks ---


class SimpleHook(HookBase):
    _name = "test-hook"
    _hook_point = HookPoint.TOOL_AFTER_RUN
    _priority = 50

    def __init__(self, name=None, hook_point=None, priority=None, func=None):
        if name:
            self._name = name
        if hook_point:
            self._hook_point = hook_point
        if priority is not None:
            self._priority = priority
        self._func = func or (lambda ctx: ctx)

    def execute(self, context: HookContext) -> HookContext:
        return self._func(context)


class TestHookRegistry:
    def test_register_and_trigger(self):
        registry = HookRegistry()
        results = []
        registry.register(SimpleHook(func=lambda ctx: (results.append("ran"), ctx)[1]))
        ctx = registry.trigger_sync(HookPoint.TOOL_AFTER_RUN, {"name": "tool1"})
        assert results == ["ran"]

    @pytest.mark.asyncio
    async def test_async_trigger(self):
        registry = HookRegistry()
        results = []
        registry.register(SimpleHook(func=lambda ctx: (results.append("async"), ctx)[1]))
        ctx = await registry.trigger(HookPoint.TOOL_AFTER_RUN, {"name": "tool1"})
        assert results == ["async"]

    def test_priority_ordering(self):
        registry = HookRegistry()
        results = []
        registry.register(SimpleHook(name="low", priority=100, func=lambda ctx: (results.append("second"), ctx)[1]))
        registry.register(SimpleHook(name="high", priority=1, func=lambda ctx: (results.append("first"), ctx)[1]))
        registry.trigger_sync(HookPoint.TOOL_AFTER_RUN)
        assert results == ["first", "second"]

    def test_stop_propagation(self):
        registry = HookRegistry()
        results = []

        def stopper(ctx):
            results.append("stop")
            ctx.stop_propagation()
            return ctx

        def after(ctx):
            results.append("should not run")
            return ctx

        registry.register(SimpleHook(name="stopper", func=stopper))
        registry.register(SimpleHook(name="after", priority=100, func=after))
        registry.trigger_sync(HookPoint.TOOL_AFTER_RUN)
        assert results == ["stop"]

    def test_error_stops_chain(self):
        registry = HookRegistry()
        results = []

        def bad(ctx):
            results.append("error")
            raise RuntimeError("boom")

        def after(ctx):
            results.append("should not run")
            return ctx

        registry.register(SimpleHook(name="bad", func=bad))
        registry.register(SimpleHook(name="after", priority=100, func=after))
        ctx = registry.trigger_sync(HookPoint.TOOL_AFTER_RUN)
        assert results == ["error"]
        assert ctx.has_error

    def test_duplicate_name_not_registered(self):
        registry = HookRegistry()
        h1 = SimpleHook(name="dup", func=lambda ctx: ctx)
        h2 = SimpleHook(name="dup", func=lambda ctx: ctx)
        registry.register(h1)
        registry.register(h2)
        assert len(registry.get_hooks(HookPoint.TOOL_AFTER_RUN)) == 1

    def test_unregister(self):
        registry = HookRegistry()
        registry.register(SimpleHook(name="removable", func=lambda ctx: ctx))
        assert registry.unregister("removable")
        assert not registry.has_hooks(HookPoint.TOOL_AFTER_RUN)

    def test_unregister_nonexistent(self):
        registry = HookRegistry()
        assert not registry.unregister("nope")

    def test_has_hooks(self):
        registry = HookRegistry()
        assert not registry.has_hooks(HookPoint.TOOL_AFTER_RUN)
        registry.register(SimpleHook(func=lambda ctx: ctx))
        assert registry.has_hooks(HookPoint.TOOL_AFTER_RUN)

    @pytest.mark.asyncio
    async def test_async_hook(self):
        registry = HookRegistry()
        results = []

        async def async_func(ctx):
            results.append("async_hook")
            return ctx

        registry.register(SimpleHook(func=async_func))
        ctx = await registry.trigger(HookPoint.TOOL_AFTER_RUN)
        assert results == ["async_hook"]

    def test_unregister_all(self):
        registry = HookRegistry()
        registry.register(SimpleHook(name="a", func=lambda ctx: ctx))
        registry.register(SimpleHook(name="b", hook_point=HookPoint.AGENT_CHAT_START, func=lambda ctx: ctx))
        registry.unregister_all()
        assert not registry.has_hooks(HookPoint.TOOL_AFTER_RUN)
        assert not registry.has_hooks(HookPoint.AGENT_CHAT_START)


class TestHookDecorator:
    def test_hook_decorator(self):
        @hook(HookPoint.TOOL_AFTER_RUN, name="dec-hook")
        def my_hook(context: HookContext) -> HookContext:
            return context

        assert my_hook.name == "dec-hook"
        assert my_hook.hook_point == HookPoint.TOOL_AFTER_RUN
        assert my_hook.priority == 50

    def test_hook_decorator_default_name(self):
        @hook(HookPoint.AGENT_CHAT_START)
        def auto_named(context: HookContext) -> HookContext:
            return context

        assert auto_named.name == "auto_named"

    def test_hook_decorator_execute(self):
        @hook(HookPoint.TOOL_AFTER_RUN)
        def add_data(context: HookContext) -> HookContext:
            context.data["added"] = True
            return context

        ctx = HookContext(hook_point=HookPoint.TOOL_AFTER_RUN, data={})
        result = add_data.execute(ctx)
        assert result.data["added"] is True


class TestAsyncHookDecorator:
    @pytest.mark.asyncio
    async def test_async_hook_decorator(self):
        @async_hook(HookPoint.AGENT_CHAT_END, name="async-dec")
        async def my_async_hook(context: HookContext) -> HookContext:
            context.data["async_ran"] = True
            return context

        ctx = HookContext(hook_point=HookPoint.AGENT_CHAT_END, data={})
        result = await my_async_hook.execute(ctx)
        assert result.data["async_ran"] is True


class TestHookBuilder:
    def test_build(self):
        built = (
            HookBuilder(HookPoint.TOOL_AFTER_RUN)
            .name("built-hook")
            .priority(10)
            .execute(lambda ctx: ctx)
            .build()
        )
        assert built.name == "built-hook"
        assert built.priority == 10
        assert built.hook_point == HookPoint.TOOL_AFTER_RUN

    def test_build_with_condition(self):
        built = (
            HookBuilder(HookPoint.TOOL_AFTER_RUN)
            .name("cond-hook")
            .condition(lambda ctx: ctx.data.get("run") is True)
            .execute(lambda ctx: ctx)
            .build()
        )
        ctx = HookContext(hook_point=HookPoint.TOOL_AFTER_RUN, data={"run": True})
        assert built.should_execute(ctx)
        ctx2 = HookContext(hook_point=HookPoint.TOOL_AFTER_RUN, data={"run": False})
        assert not built.should_execute(ctx2)

    def test_build_without_execute_raises(self):
        with pytest.raises(ValueError, match="Execute function is required"):
            HookBuilder(HookPoint.TOOL_AFTER_RUN).build()


class TestPluginRegistry:
    def test_register_and_get(self):
        registry = PluginRegistry()
        info = PluginInfo(name="test-plugin", path="/tmp/test")
        registry.register(info)
        assert registry.get("test-plugin") is info

    def test_unregister(self):
        registry = PluginRegistry()
        registry.register(PluginInfo(name="p", path="/tmp"))
        assert registry.unregister("p")
        assert registry.get("p") is None

    def test_all(self):
        registry = PluginRegistry()
        registry.register(PluginInfo(name="a", path="/a"))
        registry.register(PluginInfo(name="b", path="/b"))
        assert len(registry.all()) == 2

    def test_list_names(self):
        registry = PluginRegistry()
        registry.register(PluginInfo(name="x", path="/x"))
        assert registry.list_names() == ["x"]

    def test_list_by_state(self):
        registry = PluginRegistry()
        registry.register(PluginInfo(name="disc", path="/d", state=PluginState.DISCOVERED))
        registry.register(PluginInfo(name="enb", path="/e", state=PluginState.ENABLED))
        enabled = registry.list_by_state(PluginState.ENABLED)
        assert len(enabled) == 1
        assert enabled[0].name == "enb"

    def test_enabled(self):
        registry = PluginRegistry()
        registry.register(PluginInfo(name="e", path="/e", state=PluginState.ENABLED))
        registry.register(PluginInfo(name="d", path="/d", state=PluginState.DISCOVERED))
        assert len(registry.enabled()) == 1

    def test_has_plugin(self):
        registry = PluginRegistry()
        assert not registry.has_plugin("none")
        registry.register(PluginInfo(name="p", path="/p"))
        assert registry.has_plugin("p")

    def test_clear(self):
        registry = PluginRegistry()
        registry.register(PluginInfo(name="p", path="/p"))
        registry.clear()
        assert len(registry.all()) == 0


class TestPluginInfo:
    def test_is_loaded(self):
        info = PluginInfo(name="p", path="/p", state=PluginState.LOADED)
        assert info.is_loaded

    def test_is_enabled(self):
        info = PluginInfo(name="p", path="/p", state=PluginState.ENABLED)
        assert info.is_enabled

    def test_has_error(self):
        info = PluginInfo(name="p", path="/p", state=PluginState.ERROR)
        assert info.has_error

    def test_has_error_with_message(self):
        info = PluginInfo(name="p", path="/p", error="something broke")
        assert info.has_error

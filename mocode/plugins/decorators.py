"""Decorators for plugin system"""

from typing import Callable

from .base import Hook, HookContext, HookPoint


def hook(
    hook_point: HookPoint,
    name: str | None = None,
    priority: int = 50,
) -> Callable[[Callable[[HookContext], HookContext]], Hook]:
    """Decorator to create a hook from a function

    Usage:
        @hook(HookPoint.TOOL_AFTER_RUN, priority=25)
        def log_tool(context: HookContext) -> HookContext:
            print(f"Tool executed: {context.data['name']}")
            return context
    """

    def decorator(func: Callable[[HookContext], HookContext]) -> Hook:
        class FunctionHook(Hook):
            @property
            def _name(self) -> str:
                return name or func.__name__

            @property
            def name(self) -> str:
                return self._name

            @property
            def hook_point(self) -> HookPoint:
                return hook_point

            @property
            def priority(self) -> int:
                return priority

            def execute(self, context: HookContext) -> HookContext:
                return func(context)

            def should_execute(self, context: HookContext) -> bool:
                return True

        return FunctionHook()

    return decorator


def async_hook(
    hook_point: HookPoint,
    name: str | None = None,
    priority: int = 50,
) -> Callable[[Callable[[HookContext], "HookContext"]], "Hook"]:
    """Decorator to create an async hook from a function

    Note: The hook registry's trigger method is async and will await
    coroutine results if the execute method returns a coroutine.

    Usage:
        @async_hook(HookPoint.AGENT_CHAT_END, priority=25)
        async def log_chat(context: HookContext) -> HookContext:
            await async_log(f"Chat completed: {context.result}")
            return context
    """
    import asyncio

    def decorator(func: Callable[[HookContext], "HookContext"]) -> "Hook":
        class AsyncFunctionHook(Hook):
            @property
            def _name(self) -> str:
                return name or func.__name__

            @property
            def name(self) -> str:
                return self._name

            @property
            def hook_point(self) -> HookPoint:
                return hook_point

            @property
            def priority(self) -> int:
                return priority

            def execute(self, context: HookContext) -> HookContext:
                # Check if the function is a coroutine function
                if asyncio.iscoroutinefunction(func):
                    # Return a coroutine that will be awaited by trigger
                    async def async_execute():
                        return await func(context)

                    # We need to handle this differently - the registry
                    # will need to check if execute returns a coroutine
                    return async_execute()
                return func(context)

            def should_execute(self, context: HookContext) -> bool:
                return True

        return AsyncFunctionHook()

    return decorator


class HookBuilder:
    """Builder for creating hooks with fluent API"""

    def __init__(self, hook_point: HookPoint):
        self._hook_point = hook_point
        self._name: str | None = None
        self._priority: int = 50
        self._condition: Callable[[HookContext], bool] | None = None
        self._execute: Callable[[HookContext], HookContext] | None = None

    def name(self, name: str) -> "HookBuilder":
        """Set hook name"""
        self._name = name
        return self

    def priority(self, priority: int) -> "HookBuilder":
        """Set hook priority"""
        self._priority = priority
        return self

    def condition(self, condition: Callable[[HookContext], bool]) -> "HookBuilder":
        """Set condition for hook execution"""
        self._condition = condition
        return self

    def execute(self, func: Callable[[HookContext], HookContext]) -> "HookBuilder":
        """Set execution function"""
        self._execute = func
        return self

    def build(self) -> Hook:
        """Build the hook"""
        if self._execute is None:
            raise ValueError("Execute function is required")

        execute_func = self._execute
        condition_func = self._condition
        hook_name = self._name or "anonymous_hook"
        hook_priority = self._priority
        hook_point = self._hook_point

        class BuiltHook(Hook):
            @property
            def name(self) -> str:
                return hook_name

            @property
            def hook_point(self) -> HookPoint:
                return hook_point

            @property
            def priority(self) -> int:
                return hook_priority

            def execute(self, context: HookContext) -> HookContext:
                return execute_func(context)

            def should_execute(self, context: HookContext) -> bool:
                if condition_func is None:
                    return True
                return condition_func(context)

        return BuiltHook()

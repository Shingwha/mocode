"""Hook and Plugin registries"""

import inspect
from collections import defaultdict
from typing import Any

from .base import Hook, HookContext, HookPoint, Plugin, PluginInfo, PluginState


class HookRegistry:
    """Registry for hooks - instance-based for multi-tenant support"""

    def __init__(self):
        self._hooks: dict[HookPoint, list[Hook]] = defaultdict(list)

    def register(self, hook: Hook) -> None:
        """Register a hook, sorted by priority"""
        hooks = self._hooks[hook.hook_point]

        # Avoid duplicate registration
        for existing in hooks:
            if existing.name == hook.name:
                return

        hooks.append(hook)
        hooks.sort(key=lambda h: h.priority)

    def unregister(self, name: str) -> bool:
        """Unregister a hook by name

        Returns:
            True if hook was found and removed
        """
        for hook_point in self._hooks:
            hooks = self._hooks[hook_point]
            for i, hook in enumerate(hooks):
                if hook.name == name:
                    hooks.pop(i)
                    return True
        return False

    def unregister_all(self) -> None:
        """Unregister all hooks"""
        self._hooks.clear()

    def get_hooks(self, hook_point: HookPoint) -> list[Hook]:
        """Get all hooks for a specific hook point"""
        return self._hooks[hook_point].copy()

    def has_hooks(self, hook_point: HookPoint) -> bool:
        """Check if any hooks are registered for a hook point"""
        return bool(self._hooks[hook_point])

    async def trigger(
        self,
        hook_point: HookPoint,
        data: dict[str, Any] | None = None,
        initial_result: Any = None,
    ) -> HookContext:
        """Trigger all hooks for a hook point

        Args:
            hook_point: The hook point to trigger
            data: Context data passed to hooks
            initial_result: Initial result value

        Returns:
            HookContext with final result and state
        """
        context = HookContext(
            hook_point=hook_point,
            data=data or {},
            result=initial_result,
        )

        for hook in self._hooks[hook_point]:
            if not context._proceed:
                break

            try:
                if hook.should_execute(context):
                    result = hook.execute(context)
                    if inspect.iscoroutine(result):
                        context = await result
                    else:
                        context = result
            except Exception as e:
                context.set_error(e)
                break

        return context

    def trigger_sync(
        self,
        hook_point: HookPoint,
        data: dict[str, Any] | None = None,
        initial_result: Any = None,
    ) -> HookContext:
        """Synchronous version of trigger for non-async contexts"""
        context = HookContext(
            hook_point=hook_point,
            data=data or {},
            result=initial_result,
        )

        for hook in self._hooks[hook_point]:
            if not context._proceed:
                break

            try:
                if hook.should_execute(context):
                    result = hook.execute(context)
                    if inspect.iscoroutine(result):
                        import logging

                        logging.getLogger(__name__).warning(
                            f"Async hook '{hook.name}' called in sync context, skipping"
                        )
                    else:
                        context = result
            except Exception as e:
                context.set_error(e)
                break

        return context


class PluginRegistry:
    """Registry for plugins - tracks discovered and loaded plugins"""

    def __init__(self):
        self._plugins: dict[str, PluginInfo] = {}

    def register(self, info: PluginInfo) -> None:
        """Register a plugin info"""
        self._plugins[info.name] = info

    def unregister(self, name: str) -> bool:
        """Unregister a plugin by name

        Returns:
            True if plugin was found and removed
        """
        if name in self._plugins:
            del self._plugins[name]
            return True
        return False

    def get(self, name: str) -> PluginInfo | None:
        """Get plugin info by name"""
        return self._plugins.get(name)

    def all(self) -> list[PluginInfo]:
        """Get all registered plugin infos"""
        return list(self._plugins.values())

    def list_names(self) -> list[str]:
        """List all registered plugin names"""
        return list(self._plugins.keys())

    def list_by_state(self, state: PluginState) -> list[PluginInfo]:
        """Get plugins by state"""
        return [p for p in self._plugins.values() if p.state == state]

    def enabled(self) -> list[PluginInfo]:
        """Get all enabled plugins"""
        return self.list_by_state(PluginState.ENABLED)

    def has_plugin(self, name: str) -> bool:
        """Check if plugin exists"""
        return name in self._plugins

    def clear(self) -> None:
        """Clear all registered plugins"""
        self._plugins.clear()

"""SDK Entry Point - Thin facade over MocodeCore

This module provides a convenient entry point for using mocode as a library.
All business logic is delegated to MocodeCore in the core layer.
"""

import asyncio
from typing import TYPE_CHECKING, Callable

from .core import (
    AsyncAgent,
    Config,
    EventBus,
    EventType,
    EventBus,
    InterruptToken,
    PermissionHandler,
    PermissionMatcher,
    DefaultPermissionHandler,
    Session,
    SessionManager,
    MocodeCore,
)
from .core.prompt import PromptBuilder
from .plugins import HookRegistry, PluginManager, PluginInfo


class MocodeClient:
    """Convenient SDK entry point - thin facade over MocodeCore

    For embedding mocode into other applications with:
    - In-memory configuration (no filesystem required)
    - Isolated event bus (multi-tenant support)
    - Flexible permission handling
    - Auto-loading Skills
    - Interrupt support via interrupt() method
    - Optional config persistence
    - Custom Prompt building
    - Plugin system support (Hooks and Plugins)
    """

    def __init__(
        self,
        config: dict | None = None,
        config_path: str | None = None,
        event_bus: EventBus | None = None,
        permission_handler: PermissionHandler | None = None,
        permission_matcher: PermissionMatcher | None = None,
        interrupt_token: InterruptToken | None = None,
        persistence: bool = True,
        auto_register_tools: bool = True,
        workdir: str | None = None,
        prompt_builder: "PromptBuilder | None" = None,
        hook_registry: HookRegistry | None = None,
        plugin_manager: PluginManager | None = None,
        auto_discover_plugins: bool = True,
    ):
        """Initialize mocode client

        Args:
            config: Config dict (in-memory mode), takes priority over config_path
            config_path: Config file path
            event_bus: Event bus instance, uses default if None
            permission_handler: Permission handler, uses default (auto-allow) if None
            permission_matcher: Permission matcher for tool permission checks
            interrupt_token: Interrupt signal, creates new instance if None
            persistence: Whether to persist config changes
            auto_register_tools: Whether to auto-register tools (idempotent)
            workdir: Working directory for session isolation, defaults to cwd
            prompt_builder: Prompt builder, uses default if None
            hook_registry: Hook registry, creates new instance if None
            plugin_manager: Plugin manager, creates new instance if None
            auto_discover_plugins: Whether to auto-discover and enable plugins
        """
        # Load configuration
        loaded_config = Config.load(path=config_path, data=config)

        # Initialize event bus
        bus = event_bus or EventBus()

        # Initialize interrupt token
        token = interrupt_token or InterruptToken()

        # Create core orchestrator (handles all initialization)
        self._core = MocodeCore(
            config=loaded_config,
            event_bus=bus,
            interrupt_token=token,
            permission_handler=permission_handler,
            permission_matcher=permission_matcher,
            prompt_builder=prompt_builder,
            hook_registry=hook_registry,
            plugin_manager=plugin_manager,
            workdir=workdir,
            persistence=persistence,
            auto_register_tools=auto_register_tools,
            auto_discover_plugins=auto_discover_plugins,
        )

        # Store event loop reference for cross-thread async calls
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # Chat operations

    async def chat(self, message: str) -> str:
        """Send a message and get response"""
        return await self._core.chat(message)

    def interrupt(self):
        """Interrupt current operation"""
        self._core.interrupt()

    def clear_history(self):
        """Clear conversation history"""
        self._core.clear_history()

    # Event handling

    def on_event(self, event_type: EventType, handler: Callable):
        """Subscribe to events"""
        self._core.event_bus.on(event_type, handler)

    def off_event(self, event_type: EventType, handler: Callable):
        """Unsubscribe from events"""
        self._core.event_bus.off(event_type, handler)

    # Session management

    @property
    def session_manager(self) -> SessionManager:
        """Session manager (lazy-loaded in core)"""
        return SessionManager(self._core.workdir)

    def list_sessions(self) -> list[Session]:
        """List all sessions for current workdir"""
        return self._core.list_sessions()

    def save_session(self) -> Session:
        """Save current conversation as a session"""
        return self._core.save_session()

    def load_session(self, session_id: str) -> Session | None:
        """Load a session by ID"""
        return self._core.load_session(session_id)

    def clear_history_with_save(self) -> Session | None:
        """Clear history and auto-save"""
        return self._core.save_current_session_and_clear()

    # Model/Provider management

    @property
    def current_model(self) -> str:
        """Current model"""
        return self._core.current_model

    @property
    def current_provider(self) -> str:
        """Current provider key"""
        return self._core.current_provider

    @property
    def providers(self) -> dict:
        """All providers"""
        return self._core.providers

    @property
    def models(self) -> list[str]:
        """Current provider's models"""
        return self._core.models

    def get_provider_models(self, provider_key: str) -> list[str]:
        """Get models for a specific provider"""
        prov = self.providers.get(provider_key)
        return prov.models if prov else []

    def save_config(self) -> None:
        """Manually save config (if persistence enabled)"""
        if self._core.persistence_enabled:
            self._core._config_manager.save()

    def set_model(self, model: str, provider: str | None = None):
        """Switch model"""
        self._core.set_model(model, provider)

    def set_provider(self, provider_key: str, model: str | None = None) -> None:
        """Switch provider"""
        self._core.set_provider(provider_key, model)

    def add_provider(
        self,
        key: str,
        name: str,
        base_url: str,
        api_key: str = "",
        models: list[str] | None = None,
        set_current: bool = False,
    ) -> None:
        """Add a new provider"""
        self._core.add_provider(key, name, base_url, api_key, models, set_current)

    def add_model(
        self, model: str, provider: str | None = None, set_current: bool = False
    ) -> None:
        """Add model to provider"""
        self._core.add_model(model, provider, set_current)

    def remove_provider(self, key: str) -> None:
        """Remove provider"""
        self._core.remove_provider(key)

    def update_provider(
        self,
        key: str,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Update provider config"""
        self._core.update_provider(key, name, base_url, api_key)

    def remove_model(self, model: str, provider: str | None = None) -> None:
        """Remove model from provider"""
        self._core.remove_model(model, provider)

    # Prompt management

    @property
    def prompt_builder(self) -> "PromptBuilder":
        """Prompt builder"""
        return self._core.prompt_builder

    def rebuild_system_prompt(
        self, context: dict | None = None, clear_history: bool = False
    ) -> None:
        """Rebuild system prompt"""
        self._core.rebuild_system_prompt(context, clear_history)

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        """Directly update system prompt"""
        self._core.update_system_prompt(prompt, clear_history)

    # Plugin management

    def list_plugins(self) -> list[PluginInfo]:
        """List all discovered plugins"""
        return self._core.list_plugins()

    async def enable_plugin(self, name: str) -> bool:
        """Enable a plugin"""
        return await self._core.enable_plugin(name)

    async def disable_plugin(self, name: str) -> bool:
        """Disable a plugin"""
        return await self._core.disable_plugin(name)

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get plugin info by name"""
        return self._core.get_plugin_info(name)

    def discover_plugins(self) -> list[PluginInfo]:
        """Re-discover plugins after installation"""
        return self._core.discover_plugins()

    # Properties

    @property
    def workdir(self) -> str:
        """Working directory"""
        return self._core.workdir

    @property
    def persistence_enabled(self) -> bool:
        """Whether persistence is enabled"""
        return self._core.persistence_enabled

    @property
    def event_bus(self) -> EventBus:
        """Event bus"""
        return self._core.event_bus

    @property
    def config(self) -> Config:
        """Configuration"""
        return self._core.config

    @property
    def agent(self) -> AsyncAgent:
        """Agent instance"""
        return self._core._agent_facade.agent


# Convenience exports
__all__ = [
    "MocodeClient",
    "EventBus",
    "EventType",
    "PermissionMatcher",
    "PermissionHandler",
    "DefaultPermissionHandler",
    "InterruptToken",
    "Session",
    "SessionManager",
    # Prompt system (re-exported from core)
    "PromptBuilder",
    "StaticSection",
    "DynamicSection",
    "default_prompt",
    "minimal_prompt",
    "custom_prompt",
    # Plugin system
    "HookRegistry",
    "PluginManager",
    "PluginInfo",
    # Core exports
    "MocodeCore",
]

# Re-export prompt types for convenience
from .core.prompt import (
    DynamicSection,
    PromptBuilder,
    StaticSection,
    custom_prompt,
    default_prompt,
    minimal_prompt,
)

"""Mocode Core - Central orchestrator for business logic"""

import os
from typing import TYPE_CHECKING, Any, Callable

from .agent_facade import AgentFacade
from .config import Config
from .events import EventBus, get_event_bus
from .interrupt import InterruptToken
from .permission import DefaultPermissionHandler, PermissionHandler, PermissionMatcher
from .session import Session, SessionManager
from .session_coordinator import SessionCoordinator
from .prompt import PromptBuilder, default_prompt
from ..plugins import HookRegistry, PluginInfo, PluginManager
from ..providers import AsyncOpenAIProvider
from ..tools import register_all_tools
from ..skills import SkillManager
from .plugin_coordinator import PluginCoordinator

if TYPE_CHECKING:
    from .prompt import PromptBuilder


class MocodeCore:
    """Central orchestrator for mocode business logic

    Coordinates all components and provides high-level operations
    that the SDK layer delegates to. Handles persistence callbacks
    and maintains consistent state across components.
    """

    def __init__(
        self,
        config: Config,
        event_bus: EventBus,
        interrupt_token: InterruptToken,
        permission_handler: PermissionHandler | None = None,
        permission_matcher: PermissionMatcher | None = None,
        prompt_builder: "PromptBuilder | None" = None,
        hook_registry: HookRegistry | None = None,
        plugin_manager: PluginManager | None = None,
        workdir: str | None = None,
        persistence: bool = True,
        auto_register_tools: bool = True,
        auto_discover_plugins: bool = True,
    ):
        """Initialize mocode core

        Args:
            config: Configuration instance
            event_bus: Event bus for notifications
            interrupt_token: Token for cancellation
            permission_handler: Handler for permission prompts
            permission_matcher: Matcher for permission checks
            prompt_builder: Builder for system prompts
            hook_registry: Registry for hooks
            plugin_manager: Plugin manager instance
            workdir: Working directory
            persistence: Whether to persist config changes
            auto_register_tools: Whether to auto-register tools
            auto_discover_plugins: Whether to auto-discover plugins
        """
        # Register tools (idempotent)
        if auto_register_tools:
            register_all_tools()

        # Core components
        self._config = config
        self._event_bus = event_bus
        self._interrupt_token = interrupt_token
        self._persistence = persistence
        self._workdir = workdir or os.getcwd()

        # Permission handling
        self._permission_handler = permission_handler or DefaultPermissionHandler()
        self._permission_matcher = permission_matcher

        # Hook registry
        self._hook_registry = hook_registry or HookRegistry()

        # Prompt builder
        self._prompt_builder = prompt_builder or default_prompt()

        # Session management
        self._session_manager = SessionManager(self._workdir)
        self._session_coordinator = SessionCoordinator(self._session_manager)

        # Agent facade
        self._agent_facade = AgentFacade(
            config=self._config,
            event_bus=self._event_bus,
            interrupt_token=self._interrupt_token,
            permission_handler=self._permission_handler,
            permission_matcher=self._permission_matcher,
            hook_registry=self._hook_registry,
            prompt_builder=self._prompt_builder,
            workdir=self._workdir,
            session_coordinator=self._session_coordinator,
        )

        # Plugin management
        self._plugin_manager = plugin_manager or PluginManager(
            hook_registry=self._hook_registry
        )
        self._plugin_coordinator = PluginCoordinator(
            plugin_manager=self._plugin_manager,
            config=self._config,
            on_change=self._save_config,
        )

        # Auto-discover plugins
        if auto_discover_plugins:
            self._plugin_coordinator.initialize(
                disabled_list=self._config.plugins.disabled
            )

    def _save_config(self) -> None:
        """Save config if persistence is enabled"""
        if self._persistence:
            self._config.save()

    # Chat operations

    async def chat(self, message: str) -> str:
        """Send a message and get response

        Args:
            message: User message

        Returns:
            Assistant response
        """
        return await self._agent_facade.chat(message)

    def interrupt(self) -> None:
        """Interrupt current operation"""
        self._interrupt_token.interrupt()

    def clear_history(self) -> None:
        """Clear conversation history"""
        self._agent_facade.clear_history()

    # Session operations

    def list_sessions(self) -> list[Session]:
        """List all sessions for current workdir"""
        return self._session_coordinator.list_sessions()

    def save_session(self) -> Session:
        """Save current conversation as a session"""
        return self._session_coordinator.save_session(
            messages=self._agent_facade.messages,
            model=self.current_model,
            provider=self.current_provider,
        )

    def load_session(self, session_id: str) -> Session | None:
        """Load a session by ID"""
        session = self._session_coordinator.load_session(session_id)
        if session:
            self._agent_facade.messages = session.messages.copy()
        return session

    def save_current_session_and_clear(self) -> Session | None:
        """Save current session and clear history"""
        saved = None
        if self._agent_facade.messages:
            saved = self.save_session()
        self.clear_history()
        return saved

    # Config operations

    def set_model(self, model: str, provider: str | None = None) -> None:
        """Set current model"""
        was_current = self._config.set_model(model, provider)

        # Update agent if current provider changed
        if provider or was_current:
            self._agent_facade.switch_provider(self._config)

        self._save_config()

    def set_provider(self, provider_key: str, model: str | None = None) -> None:
        """Switch to a provider"""
        self._config.set_provider(provider_key, model)
        self._agent_facade.switch_provider(self._config)
        self._save_config()

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
        self._config.add_provider(key, name, base_url, api_key, models)

        if set_current:
            self.set_provider(key)
        else:
            self._save_config()

    def add_model(
        self,
        model: str,
        provider: str | None = None,
        set_current: bool = False,
    ) -> None:
        """Add a model to a provider"""
        self._config.add_model(model, provider)

        if set_current:
            if provider:
                self.set_provider(provider, model)
            else:
                self.set_model(model)
        else:
            self._save_config()

    def remove_provider(self, key: str) -> None:
        """Remove a provider"""
        new_current = self._config.remove_provider(key)
        if new_current:
            self._agent_facade.switch_provider(self._config)
        self._save_config()

    def remove_model(self, model: str, provider: str | None = None) -> None:
        """Remove a model from a provider"""
        new_model = self._config.remove_model(model, provider)
        if new_model:
            self._agent_facade.switch_provider(self._config)
        self._save_config()

    def update_provider(
        self,
        key: str,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Update provider configuration"""
        was_current = self._config.update_provider(key, name, base_url, api_key)
        if was_current:
            self._agent_facade.switch_provider(self._config)
        self._save_config()

    # Prompt operations

    def rebuild_system_prompt(
        self,
        context: dict[str, Any] | None = None,
        clear_history: bool = False,
    ) -> None:
        """Rebuild system prompt"""
        self._agent_facade.rebuild_prompt(context, clear_history)

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        """Directly update system prompt"""
        self._agent_facade.update_prompt(prompt, clear_history)

    # Plugin operations

    def list_plugins(self) -> list[PluginInfo]:
        """List all discovered plugins"""
        return self._plugin_coordinator.list_plugins()

    def enable_plugin(self, name: str) -> bool:
        """Enable a plugin"""
        return self._plugin_coordinator.enable_plugin(name)

    def disable_plugin(self, name: str) -> bool:
        """Disable a plugin"""
        return self._plugin_coordinator.disable_plugin(name)

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get plugin info by name"""
        return self._plugin_coordinator.get_plugin_info(name)

    # Properties

    @property
    def current_model(self) -> str:
        """Current model"""
        return self._config.model

    @property
    def current_provider(self) -> str:
        """Current provider key"""
        return self._config.current.provider

    @property
    def providers(self) -> dict:
        """All providers"""
        return self._config.providers

    @property
    def models(self) -> list[str]:
        """Current provider's models"""
        return self._config.models

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Message history"""
        return self._agent_facade.messages

    @property
    def event_bus(self) -> EventBus:
        """Event bus"""
        return self._event_bus

    @property
    def prompt_builder(self) -> "PromptBuilder":
        """Prompt builder"""
        return self._prompt_builder

    @property
    def workdir(self) -> str:
        """Working directory"""
        return self._workdir

    @property
    def config(self) -> Config:
        """Configuration"""
        return self._config

    @property
    def persistence_enabled(self) -> bool:
        """Whether persistence is enabled"""
        return self._persistence

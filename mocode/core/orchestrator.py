"""Mocode Core - Central orchestrator for business logic"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .agent_facade import AgentFacade
from .config import Config
from .events import EventBus
from .interrupt import InterruptToken
from .permission import DefaultPermissionHandler, PermissionHandler, PermissionMatcher
from .session import Session, SessionManager
from .prompt import PromptBuilder, default_prompt
from ..plugins import HookRegistry, PluginInfo, PluginManager
from ..providers import AsyncOpenAIProvider
from ..tools import register_all_tools
from ..skills import SkillManager
from .plugin_coordinator import PluginCoordinator

if TYPE_CHECKING:
    from .prompt import PromptBuilder


@dataclass
class SessionState:
    """Tracks current session state"""

    current_session_id: str | None = None
    has_unsaved_changes: bool = False


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

        # Session management (inlined from SessionCoordinator)
        self._session_manager = SessionManager(self._workdir)
        self._session_state = SessionState()

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
            on_chat=self._mark_unsaved,
            on_clear_history=self._clear_session_state,
        )

        # Plugin management
        self._plugin_manager = plugin_manager or PluginManager(
            hook_registry=self._hook_registry
        )
        self._plugin_coordinator = PluginCoordinator(
            plugin_manager=self._plugin_manager,
            config=self._config,
        )

        # Auto-discover plugins
        if auto_discover_plugins:
            self._plugin_coordinator.initialize()

    def _save_config(self) -> None:
        """Save config if persistence is enabled"""
        if self._persistence:
            self._config.save()

    def _mark_unsaved(self) -> None:
        """Mark session as having unsaved changes"""
        self._session_state.has_unsaved_changes = True

    def _clear_session_state(self) -> None:
        """Clear session state"""
        self._session_state.current_session_id = None
        self._session_state.has_unsaved_changes = False

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

    # Session operations (inlined from SessionCoordinator)

    @property
    def current_session_id(self) -> str | None:
        """Current session ID"""
        return self._session_state.current_session_id

    @property
    def has_unsaved_changes(self) -> bool:
        """Whether there are unsaved changes"""
        return self._session_state.has_unsaved_changes

    def list_sessions(self) -> list[Session]:
        """List all sessions for current workdir"""
        return self._session_manager.list_sessions()

    def save_session(self) -> Session:
        """Save current conversation as a session"""
        messages = self._agent_facade.messages
        model = self.current_model
        provider = self.current_provider

        if self._session_state.current_session_id:
            session = self._session_manager.update_session(
                session_id=self._session_state.current_session_id,
                messages=messages,
                model=model,
                provider=provider,
            )
            if session:
                self._session_state.has_unsaved_changes = False
                return session

        session = self._session_manager.save_session(
            messages=messages,
            model=model,
            provider=provider,
        )
        self._session_state.current_session_id = session.id
        self._session_state.has_unsaved_changes = False
        return session

    def load_session(self, session_id: str) -> Session | None:
        """Load a session by ID"""
        session = self._session_manager.load_session(session_id)
        if session:
            self._session_state.current_session_id = session_id
            self._session_state.has_unsaved_changes = False
            self._agent_facade.messages = session.messages.copy()
        return session

    def delete_session(self, session_id: str) -> bool:
        """Delete a session"""
        result = self._session_manager.delete_session(session_id)
        if result and self._session_state.current_session_id == session_id:
            self._session_state.current_session_id = None
        return result

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
        success = self._plugin_coordinator.enable_plugin(name)
        if success:
            self._save_config()
        return success

    def disable_plugin(self, name: str) -> bool:
        """Disable a plugin"""
        success = self._plugin_coordinator.disable_plugin(name)
        if success:
            self._save_config()
        return success

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

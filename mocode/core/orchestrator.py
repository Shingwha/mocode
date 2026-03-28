"""Mocode Core - Central orchestrator for business logic"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .agent import AsyncAgent
from .config import Config
from .events import EventBus, EventType
from .interrupt import InterruptToken
from .message_queue import MessageQueue
from .permission import DefaultPermissionHandler, PermissionHandler, PermissionMatcher
from .session import Session, SessionManager
from .prompt import PromptBuilder, default_prompt
from ..plugins import HookRegistry, PluginInfo, PluginManager
from ..plugins.context import PluginContext
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

    Coordinates all components and provides high-level operations.
    Can be used directly as both CLI backend and SDK entry point.
    """

    def __init__(
        self,
        config: Config | dict | None = None,
        config_path: str | None = None,
        event_bus: EventBus | None = None,
        interrupt_token: InterruptToken | None = None,
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
            config: Config instance, dict (in-memory mode), or None (load from file)
            config_path: Config file path (used when config is None)
            event_bus: Event bus for notifications (created if None)
            interrupt_token: Token for cancellation (created if None)
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

        # Normalize config parameter
        if config is None or isinstance(config, dict):
            self._config = Config.load(path=config_path, data=config)
        else:
            self._config = config

        # Create defaults for optional components
        self._event_bus = event_bus or EventBus()
        self._interrupt_token = interrupt_token or InterruptToken()
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
        self._session_state = SessionState()

        # Agent
        self._provider = AsyncOpenAIProvider(
            base_url=self._config.base_url or None,
            api_key=self._config.api_key,
            model=self._config.model,
        )
        self._agent = self._create_agent()

        # Wire config persistence after agent is created (so _on_config_changed can call _switch_provider)
        self._config.set_persistence(persistence, self._on_config_changed)

        # Message queue for external injections
        self._message_queue = MessageQueue(
            chat_fn=self._chat_with_agent,
            event_bus=self._event_bus,
            mark_unsaved_fn=self._mark_unsaved,
        )

        # Plugin management
        self._plugin_manager = plugin_manager or PluginManager(
            hook_registry=self._hook_registry,
            create_plugin_context=self.create_plugin_context,
            prompt_builder=self._prompt_builder,
        )
        self._plugin_coordinator = PluginCoordinator(
            plugin_manager=self._plugin_manager,
            config=self._config,
        )

        # Auto-discover plugins
        if auto_discover_plugins:
            self._plugin_coordinator.initialize()
            # Rebuild prompt to include plugin sections
            self._rebuild_prompt()

    def _on_config_changed(self) -> None:
        """Called after config changes are persisted.
        Updates agent provider to match new config."""
        self._switch_provider()

    def _mark_unsaved(self) -> None:
        """Mark session as having unsaved changes"""
        self._session_state.has_unsaved_changes = True

    def _clear_session_state(self) -> None:
        """Clear session state"""
        self._session_state.current_session_id = None
        self._session_state.has_unsaved_changes = False

    def _create_agent(self) -> AsyncAgent:
        """Create a new agent instance"""
        skill_manager = SkillManager.get_instance()
        system_prompt = self._prompt_builder.context(
            skill_manager=skill_manager,
            cwd=self._workdir,
        ).build()

        return AsyncAgent(
            provider=self._provider,
            system_prompt=system_prompt,
            max_tokens=self._config.max_tokens,
            event_bus=self._event_bus,
            interrupt_token=self._interrupt_token,
            config=self._config,
            permission_handler=self._permission_handler,
            permission_matcher=self._permission_matcher,
            hook_registry=self._hook_registry,
        )

    def _switch_provider(self) -> None:
        """Switch to a new provider based on current config"""
        self._provider = AsyncOpenAIProvider(
            base_url=self._config.base_url or None,
            api_key=self._config.api_key,
            model=self._config.model,
        )
        self._agent.update_provider(self._provider)

    def _rebuild_prompt(
        self,
        context: dict[str, Any] | None = None,
        clear_history: bool = False,
    ) -> None:
        """Rebuild system prompt"""
        ctx = context or {}
        skill_manager = SkillManager.get_instance()
        self._prompt_builder.clear_caches()
        system_prompt = self._prompt_builder.context(
            skill_manager=skill_manager,
            cwd=self._workdir,
            **ctx,
        ).build()
        self._agent.update_system_prompt(system_prompt, clear_history=clear_history)

    # Chat operations

    async def _chat_with_agent(self, message: str, conversation_id: str | None = None) -> str:
        """Internal: run a chat on the agent with optional conversation_id"""
        if conversation_id:
            self._agent.conversation_id = conversation_id
        return await self._agent.chat(message)

    async def chat(self, message: str) -> str:
        """Send a message and get response"""
        self._mark_unsaved()
        return await self._agent.chat(message)

    @property
    def is_agent_busy(self) -> bool:
        """Check if agent is processing a message"""
        return self._message_queue.is_processing

    async def inject_message(
        self, message: str, conversation_id: str | None = None
    ) -> str:
        """Inject message from external source, blocks until processed"""
        return await self._message_queue.inject(message, conversation_id)

    def queue_message(self, message: str, conversation_id: str | None = None) -> None:
        """Non-blocking: add message to queue for later processing"""
        self._message_queue.enqueue(message, conversation_id)

    def create_plugin_context(self) -> PluginContext:
        """Create context for plugins"""
        return PluginContext(
            event_bus=self._event_bus,
            on_event=self._event_bus.on,
            inject_message=self.inject_message,
            queue_message=self.queue_message,
            get_messages=lambda: self._agent.messages.copy(),
            workdir=self._workdir,
            is_agent_busy=lambda: self._message_queue.is_processing,
            current_conversation_id=self._agent.conversation_id,
        )

    def interrupt(self) -> None:
        """Interrupt current operation"""
        self._interrupt_token.interrupt()

    def clear_history(self) -> None:
        """Clear conversation history"""
        self._agent.clear()
        self._clear_session_state()

    def clear_history_with_save(self) -> Session | None:
        """Save current session if there are messages, then clear history"""
        saved = None
        if self._agent.messages:
            saved = self.save_session()
        self.clear_history()
        return saved

    def on_event(self, event_type: EventType, handler: Callable) -> None:
        """Subscribe to events"""
        self._event_bus.on(event_type, handler)

    def off_event(self, event_type: EventType, handler: Callable) -> None:
        """Unsubscribe from events"""
        self._event_bus.off(event_type, handler)

    def save_config(self) -> None:
        """Manually save config"""
        self._config.save()

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
        messages = self._agent.messages
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
            self._agent.messages = session.messages.copy()
        return session

    def delete_session(self, session_id: str) -> bool:
        """Delete a session"""
        result = self._session_manager.delete_session(session_id)
        if result and self._session_state.current_session_id == session_id:
            self._session_state.current_session_id = None
        return result

    # Config operations

    def set_model(self, model: str, provider: str | None = None) -> None:
        """Set current model"""
        was_current = self._config.set_model(model, provider)
        if provider or was_current:
            self._switch_provider()

    def set_provider(self, provider_key: str, model: str | None = None) -> None:
        """Switch to a provider"""
        self._config.set_provider(provider_key, model)
        self._switch_provider()

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

    def remove_provider(self, key: str) -> None:
        """Remove a provider"""
        new_current = self._config.remove_provider(key)
        if new_current:
            self._switch_provider()

    def remove_model(self, model: str, provider: str | None = None) -> None:
        """Remove a model from a provider"""
        new_model = self._config.remove_model(model, provider)
        if new_model:
            self._switch_provider()

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
            self._switch_provider()

    # Prompt operations

    def rebuild_system_prompt(
        self,
        context: dict[str, Any] | None = None,
        clear_history: bool = False,
    ) -> None:
        """Rebuild system prompt"""
        self._rebuild_prompt(context, clear_history)

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        """Directly update system prompt"""
        self._agent.update_system_prompt(prompt, clear_history=clear_history)

    # Plugin operations

    def list_plugins(self) -> list[PluginInfo]:
        """List all discovered plugins"""
        return self._plugin_coordinator.list_plugins()

    async def enable_plugin(self, name: str) -> bool:
        """Enable a plugin"""
        success = await self._plugin_coordinator.enable_plugin(name)
        if success:
            self._config.save()
            self._rebuild_prompt()
        return success

    async def disable_plugin(self, name: str) -> bool:
        """Disable a plugin"""
        success = await self._plugin_coordinator.disable_plugin(name)
        if success:
            self._config.save()
            self._rebuild_prompt()
        return success

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get plugin info by name"""
        return self._plugin_coordinator.get_plugin_info(name)

    def discover_plugins(self) -> list[PluginInfo]:
        """Re-discover plugins after installation"""
        return self._plugin_coordinator.discover_plugins()

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
        return self._agent.messages

    @property
    def agent(self) -> AsyncAgent:
        """Agent instance"""
        return self._agent

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

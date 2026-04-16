"""App + AppBuilder — 组合根

v0.2 关键改进：
- App 是薄组合根，持有组件引用，约 15 个方法
- AppBuilder 处理所有组件的创建和连接
- 替代旧的 MocodeCore (533 行 god class)
"""

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Self

from .config import Config
from .store import ConfigStore, FileConfigStore, InMemoryConfigStore, Session
from .provider import OpenAIProvider
from .tool import ToolRegistry
from .event import EventBus, EventType
from .interrupt import CancellationToken
from .permission import DefaultPermissionHandler, PermissionHandler, PermissionChecker
from .session import SessionManager
from .prompt import PromptBuilder, default_prompt
from .agent import Agent
from .compact import CompactManager
from .message_queue import MessageQueue
from .tools import register_all_tools


@dataclass
class SessionState:
    """Tracks current session state"""
    current_session_id: str | None = None
    has_unsaved_changes: bool = False


@dataclass
class App:
    """薄组合根 — 持有组件引用，薄层委托"""

    config: Config
    _config_store: ConfigStore
    provider: Any  # Provider
    agent: Agent
    tools: ToolRegistry
    event_bus: EventBus
    sessions: SessionManager
    cancel_token: CancellationToken
    _prompt_builder: PromptBuilder
    _compact: CompactManager | None
    _dream: Any | None = None  # DreamManager | None
    _dream_scheduler: Any | None = None  # DreamScheduler | None
    _session_state: SessionState = field(default_factory=SessionState)
    _message_queue: MessageQueue | None = None

    # -- Chat --

    async def chat(self, message: str, media: list[str] | None = None) -> str:
        self._session_state.has_unsaved_changes = True
        return await self.agent.chat(message, media)

    def interrupt(self) -> None:
        from .interrupt import InterruptReason
        self.cancel_token.cancel(InterruptReason.USER)

    # -- Config mutations --

    def set_model(self, model: str, provider: str | None = None) -> None:
        self.config.set_model(model, provider)
        self._config_store.save(self.config.to_dict())
        self._switch_provider()

    def set_provider(self, key: str, model: str | None = None) -> None:
        self.config.set_provider(key, model)
        self._config_store.save(self.config.to_dict())
        self._switch_provider()

    def add_provider(self, key: str, name: str, base_url: str, api_key: str = "", models: list[str] | None = None, set_current: bool = False) -> None:
        self.config.add_provider(key, name, base_url, api_key, models)
        self._config_store.save(self.config.to_dict())
        if set_current:
            self.set_provider(key)

    def add_model(self, model: str, provider: str | None = None, set_current: bool = False) -> None:
        self.config.add_model(model, provider)
        self._config_store.save(self.config.to_dict())
        if set_current:
            if provider:
                self.set_provider(provider, model)
            else:
                self.set_model(model)

    def remove_provider(self, key: str) -> None:
        new_current = self.config.remove_provider(key)
        self._config_store.save(self.config.to_dict())
        if new_current:
            self._switch_provider()

    def remove_model(self, model: str, provider: str | None = None) -> None:
        new_model = self.config.remove_model(model, provider)
        self._config_store.save(self.config.to_dict())
        if new_model:
            self._switch_provider()

    def update_provider(self, key: str, name: str | None = None, base_url: str | None = None, api_key: str | None = None, models: list[str] | None = None) -> None:
        was_current = self.config.update_provider(key, name, base_url, api_key, models)
        self._config_store.save(self.config.to_dict())
        if was_current:
            self._switch_provider()

    def _switch_provider(self) -> None:
        new_provider = OpenAIProvider(
            api_key=self.config.api_key,
            model=self.config.model,
            base_url=self.config.base_url or None,
        )
        self.provider = new_provider
        self.agent.update_provider(new_provider)
        if self._compact:
            self._compact.update_provider(new_provider)
        if self._dream:
            self._dream.update_provider(new_provider)

    def save_config(self) -> None:
        self._config_store.save(self.config.to_dict())

    # -- Sessions --

    def save_session(self) -> Session:
        messages = self.agent.messages
        model = self.current_model
        provider = self.current_provider

        if self._session_state.current_session_id:
            session = self.sessions.update_session(
                session_id=self._session_state.current_session_id,
                messages=messages,
                model=model,
                provider=provider,
            )
            if session:
                self._session_state.has_unsaved_changes = False
                return session

        session = self.sessions.save_session(
            messages=messages,
            model=model,
            provider=provider,
        )
        self._session_state.current_session_id = session.id
        self._session_state.has_unsaved_changes = False
        return session

    def load_session(self, session_id: str) -> Session | None:
        session = self.sessions.load_session(session_id)
        if session:
            self._session_state.current_session_id = session_id
            self._session_state.has_unsaved_changes = False
            self.agent.messages = session.messages.copy()
        return session

    def list_sessions(self) -> list[Session]:
        return self.sessions.list_sessions()

    def delete_session(self, session_id: str) -> bool:
        result = self.sessions.delete_session(session_id)
        if result and self._session_state.current_session_id == session_id:
            self._session_state.current_session_id = None
            self.agent.clear()
        return result

    def clear_history(self) -> None:
        self.agent.clear()
        self._session_state.current_session_id = None
        self._session_state.has_unsaved_changes = False

    def clear_history_with_save(self) -> Session | None:
        saved = None
        if self.agent.messages:
            saved = self.save_session()
        self.clear_history()
        return saved

    # -- Events --

    def on_event(self, event_type: EventType, handler: Callable) -> None:
        self.event_bus.on(event_type, handler)

    def off_event(self, event_type: EventType, handler: Callable) -> None:
        self.event_bus.off(event_type, handler)

    # -- Prompt --

    def rebuild_system_prompt(self, context: dict[str, Any] | None = None, clear_history: bool = False) -> None:
        ctx = context or {}
        system_prompt = self._prompt_builder.context(
            tools=self.tools,
            cwd=self.workdir,
            **ctx,
        ).build()
        self.agent.update_system_prompt(system_prompt, clear_history=clear_history)

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        self.agent.update_system_prompt(prompt, clear_history=clear_history)

    # -- Message injection --

    async def inject_message(self, message: str, conversation_id: str | None = None) -> str:
        if not self._message_queue:
            return await self.chat(message)
        return await self._message_queue.inject(message, conversation_id)

    def queue_message(self, message: str, conversation_id: str | None = None) -> None:
        if self._message_queue:
            self._message_queue.enqueue(message, conversation_id)

    # -- Compact --

    async def compact(self) -> dict:
        if self._session_state.current_session_id:
            self.save_session()

        old_count = len(self.agent.messages)
        self.agent.messages = await self._compact.compact(
            self.agent.messages, self.config.model,
        )
        self._session_state.current_session_id = None
        self._session_state.has_unsaved_changes = True
        return {
            "action": "compact_complete",
            "old_count": old_count,
            "new_count": len(self.agent.messages),
        }

    # -- Dream --

    async def dream(self) -> dict:
        if not self._dream:
            return {"skipped": True, "reason": "dream disabled"}
        result = await self._dream.dream()
        if not result.skipped:
            self.rebuild_system_prompt()
        return {
            "summaries_processed": result.summaries_processed,
            "edits_made": result.edits_made,
            "tool_calls_made": result.tool_calls_made,
            "snapshot_id": result.snapshot_id,
            "skipped": result.skipped,
        }

    def start_dream_scheduler(self) -> None:
        if not self._dream:
            return
        if self._dream_scheduler is not None:
            return
        from .dream import DreamScheduler
        self._dream_scheduler = DreamScheduler(
            dream_manager=self._dream,
            interval_seconds=self.config.dream.interval_seconds,
        )
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._dream_scheduler.start())
        except RuntimeError:
            pass

    def stop_dream_scheduler(self) -> None:
        if self._dream_scheduler is None:
            return
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._dream_scheduler.stop())
        except RuntimeError:
            pass
        self._dream_scheduler = None

    # -- Properties --

    @property
    def current_model(self) -> str:
        return self.config.model

    @property
    def current_provider(self) -> str:
        return self.config.current.provider

    @property
    def providers(self) -> dict:
        return self.config.providers

    @property
    def models(self) -> list[str]:
        return self.config.models

    @property
    def messages(self) -> list[dict]:
        return self.agent.messages

    @property
    def workdir(self) -> str:
        return self.sessions._workdir

    @property
    def dream_manager(self):
        return self._dream

    @property
    def compact_manager(self) -> CompactManager | None:
        return self._compact

    @property
    def is_agent_busy(self) -> bool:
        if self._message_queue:
            return self._message_queue.is_processing
        return False

    @property
    def token_usage(self):
        return self.agent.last_usage

    @property
    def current_session_id(self) -> str | None:
        return self._session_state.current_session_id

    @property
    def has_unsaved_changes(self) -> bool:
        return self._session_state.has_unsaved_changes


class AppBuilder:
    """构建 App 实例 — 处理所有组件的创建和连接"""

    def __init__(self):
        self._config_data: dict | None = None
        self._config_store: ConfigStore | None = None
        self._permission_handler: PermissionHandler | None = None
        self._cancel_token: CancellationToken | None = None
        self._workdir: str | None = None
        self._persistence: bool = True

    def with_config(self, data: dict) -> Self:
        self._config_data = data
        return self

    def with_config_store(self, store: ConfigStore) -> Self:
        self._config_store = store
        return self

    def with_permission_handler(self, handler: PermissionHandler) -> Self:
        self._permission_handler = handler
        return self

    def with_cancel_token(self, token: CancellationToken) -> Self:
        self._cancel_token = token
        return self

    def with_workdir(self, path: str) -> Self:
        self._workdir = path
        return self

    def without_persistence(self) -> Self:
        self._persistence = False
        return self

    def build(self) -> App:
        """组装所有组件"""
        # Config
        config = self._load_config()

        # Store
        store = self._config_store or (
            FileConfigStore() if self._persistence else InMemoryConfigStore(config.to_dict())
        )

        # Core components
        event_bus = EventBus()
        cancel_token = self._cancel_token or CancellationToken()
        workdir = self._workdir or os.getcwd()

        # Provider
        provider = OpenAIProvider(
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url or None,
        )

        # Tools (实例作用域)
        tools = ToolRegistry()
        register_all_tools(tools, config)

        # Permission
        handler = self._permission_handler or DefaultPermissionHandler()
        permission_checker = PermissionChecker(
            permission_config=config.permission,
            handler=handler,
            config=config,
        )

        # Compact
        compact = CompactManager(config.compact, provider, event_bus)

        # Prompt
        prompt_builder = default_prompt()
        system_prompt = prompt_builder.context(
            tools=tools,
            cwd=workdir,
        ).build()

        # Agent
        agent = Agent(
            provider=provider,
            system_prompt=system_prompt,
            tools=tools,
            event_bus=event_bus,
            cancel_token=cancel_token,
            permission_checker=permission_checker,
            config=config,
            compact=compact,
        )

        # Sessions
        from .store import FileSessionStore, InMemorySessionStore
        session_store = FileSessionStore() if self._persistence else InMemorySessionStore()
        sessions = SessionManager(workdir=workdir, store=session_store)

        # Dream
        dream = None
        dream_scheduler = None
        if config.dream.enabled:
            from .dream import DreamManager
            dream = DreamManager(
                config=config.dream,
                provider=provider,
                tools=tools,
                event_bus=event_bus,
            )

        # Wire events
        session_state = SessionState()

        def _on_context_compact(event):
            session_state.current_session_id = None
            session_state.has_unsaved_changes = True

        event_bus.on(EventType.CONTEXT_COMPACT, _on_context_compact)

        # Build App
        app = App(
            config=config,
            _config_store=store,
            provider=provider,
            agent=agent,
            tools=tools,
            event_bus=event_bus,
            sessions=sessions,
            cancel_token=cancel_token,
            _prompt_builder=prompt_builder,
            _compact=compact,
            _dream=dream,
            _dream_scheduler=dream_scheduler,
            _session_state=session_state,
        )

        # Message queue
        app._message_queue = MessageQueue(
            chat_fn=lambda msg, conv_id=None: agent.chat(msg),
            event_bus=event_bus,
            mark_unsaved_fn=lambda: setattr(session_state, 'has_unsaved_changes', True),
        )

        return app

    def _load_config(self) -> Config:
        if self._config_data is not None:
            return Config.from_dict(self._config_data)
        store = self._config_store or (
            FileConfigStore() if self._persistence else InMemoryConfigStore()
        )
        data = store.load()
        if data:
            return Config.from_dict(data)
        return Config()

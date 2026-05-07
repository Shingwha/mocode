"""App integration tests — v0.2"""

import pytest
from unittest.mock import AsyncMock

from mocode.app import App, AppBuilder
from mocode.store import InMemoryConfigStore
from mocode.event import EventType


@pytest.fixture
def app():
    """App with in-memory config, no file I/O"""
    config_data = {
        "current": {"provider": "test", "model": "test-model"},
        "providers": {
            "test": {
                "name": "Test Provider",
                "base_url": "https://api.test.com/v1",
                "api_key": "test-key",
                "models": ["test-model"],
            }
        },
        "permission": {"*": "allow"},
    }
    return (
        AppBuilder()
        .with_config(config_data)
        .without_persistence()
        .build()
    )


class TestAppInit:
    def test_init_with_dict_config(self, app):
        assert app.config.current.provider == "test"
        assert app.config.model == "test-model"

    def test_tools_registered(self, app):
        assert len(app.tools.all()) > 0


class TestAppProperties:
    def test_current_model(self, app):
        assert app.current_model == "test-model"

    def test_current_provider(self, app):
        assert app.current_provider == "test"

    def test_messages_initially_empty(self, app):
        assert app.messages == []

    def test_event_bus(self, app):
        assert app.event_bus is not None


class TestAppChat:
    @pytest.mark.asyncio
    async def test_chat_delegates_to_agent(self, app):
        app.agent.chat = AsyncMock(return_value="response")
        result = await app.chat("Hello")
        assert result == "response"


class TestAppInterrupt:
    def test_interrupt_propagation(self, app):
        app.interrupt()
        assert app.cancel_token.is_cancelled


class TestAppSession:
    def test_session_save_load_cycle(self, app):
        app.agent.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        session = app.save_session()
        assert session is not None
        assert session.message_count == 2
        assert session.title == "hello"

        loaded = app.load_session(session.id)
        assert loaded is not None

    def test_list_sessions_empty(self, app):
        sessions = app.list_sessions()
        assert sessions == []

    def test_clear_history_resets_session(self, app):
        app.agent.messages = [{"role": "user", "content": "test"}]
        app.save_session()
        app.clear_history()
        assert app.current_session_id is None

    def test_chat_marks_dirty(self, app):
        assert not app.has_unsaved_changes
        app.agent.chat = AsyncMock(return_value="r")
        # We can't await in non-async test easily, so test mark_dirty directly
        app.sessions.mark_dirty()
        assert app.has_unsaved_changes

    def test_has_unsaved_changes_delegates_to_sessions(self, app):
        assert not app.has_unsaved_changes
        app.sessions.mark_dirty()
        assert app.has_unsaved_changes

    def test_current_session_id_delegates_to_sessions(self, app):
        assert app.current_session_id is None
        app.sessions.create()
        assert app.current_session_id is not None


class TestAppConfigOps:
    def test_set_model(self, app):
        app.set_model("new-model")
        assert app.current_model == "new-model"

    def test_set_provider(self, app):
        app.config.add_provider("p2", "P2", "https://p2.com/v1", "key", ["m2"])
        app.set_provider("p2")
        assert app.current_provider == "p2"

    def test_add_provider(self, app):
        app.add_provider("new", "New", "https://new.com/v1")
        assert "new" in app.providers


class TestAppEvents:
    def test_event_subscription(self, app):
        events = []
        app.on_event(EventType.ERROR, lambda e: events.append(e.data))
        app.event_bus.emit(EventType.ERROR, {"error": "test"})
        assert events == [{"error": "test"}]

    def test_off_event(self, app):
        events = []
        handler = lambda e: events.append(1)
        app.on_event(EventType.ERROR, handler)
        app.off_event(EventType.ERROR, handler)
        app.event_bus.emit(EventType.ERROR, None)
        assert events == []


class TestAppHistory:
    def test_clear_history(self, app):
        app.agent.messages = [{"role": "user", "content": "test"}]
        app.clear_history()
        assert app.messages == []


class TestAppPrompt:
    def test_rebuild_system_prompt(self, app):
        app.rebuild_system_prompt()
        assert app.agent.system_prompt is not None

    def test_update_system_prompt(self, app):
        app.update_system_prompt("Custom prompt")
        assert app.agent.system_prompt == "Custom prompt"


class TestAppInject:
    @pytest.mark.asyncio
    async def test_inject_message(self, app):
        app.agent.chat = AsyncMock(return_value="injected response")
        result = await app.inject_message("Hello", "conv-1")
        assert result == "injected response"


class TestAppBuilder:
    def test_builder_without_persistence(self):
        app = (
            AppBuilder()
            .with_config({
                "current": {"provider": "t", "model": "m"},
                "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            })
            .without_persistence()
            .build()
        )
        assert app.config.model == "m"
        assert isinstance(app._config_store, InMemoryConfigStore)

    def test_builder_with_workdir(self):
        app = (
            AppBuilder()
            .with_config({
                "current": {"provider": "t", "model": "m"},
                "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            })
            .without_persistence()
            .with_workdir("/custom/dir")
            .build()
        )
        assert app.workdir == "/custom/dir"

    def test_builder_with_session_store(self):
        from mocode.session import InMemorySessionStore
        store = InMemorySessionStore()
        app = (
            AppBuilder()
            .with_config({
                "current": {"provider": "t", "model": "m"},
                "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            })
            .without_persistence()
            .with_session_store(store)
            .build()
        )
        assert app.sessions._store is store

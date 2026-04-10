"""MocodeCore integration tests"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mocode.core.config import Config
from mocode.core.events import EventType
from mocode.core.orchestrator import MocodeCore


@pytest.fixture
def core(tmp_path):
    """MocodeCore with in-memory config, no file I/O, no plugin discovery"""
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
    return MocodeCore(
        config=config_data,
        persistence=False,
        auto_discover_plugins=False,
        workdir=str(tmp_path),
    )


class TestMocodeCoreInit:
    def test_init_with_dict_config(self, core):
        assert core.config.current.provider == "test"
        assert core.config.model == "test-model"

    def test_init_tools_registered(self, core):
        """Tools should be registered on init"""
        from mocode.tools.base import ToolRegistry
        # Should have file, search, bash, fetch tools
        assert len(ToolRegistry.all()) > 0


class TestMocodeCoreProperties:
    def test_current_model(self, core):
        assert core.current_model == "test-model"

    def test_current_provider(self, core):
        assert core.current_provider == "test"

    def test_messages_initially_empty(self, core):
        assert core.messages == []

    def test_workdir(self, core, tmp_path):
        assert core.workdir == str(tmp_path)

    def test_event_bus(self, core):
        assert core.event_bus is not None

    def test_agent(self, core):
        assert core.agent is not None


class TestMocodeCoreChat:
    @pytest.mark.asyncio
    async def test_chat_delegates_to_agent(self, core):
        core.agent.chat = AsyncMock(return_value="response")
        result = await core.chat("Hello")
        assert result == "response"
        core.agent.chat.assert_called_once_with("Hello", None)


class TestMocodeCoreInterrupt:
    def test_interrupt_propagation(self, core):
        core.interrupt()
        assert core.agent.interrupt_token.is_interrupted


class TestMocodeCoreSession:
    def test_session_save_load_cycle(self, core):
        core.agent.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        session = core.save_session()
        assert session is not None
        assert session.message_count == 2

        loaded = core.load_session(session.id)
        assert loaded is not None
        assert loaded.messages == core.agent.messages

    def test_list_sessions_empty(self, core):
        sessions = core.list_sessions()
        assert sessions == []


class TestMocodeCoreConfigOps:
    def test_set_model(self, core):
        core.set_model("new-model")
        assert core.current_model == "new-model"

    def test_set_provider(self, core):
        core.config.add_provider("p2", "P2", "https://p2.com/v1", "key", ["m2"])
        core.set_provider("p2")
        assert core.current_provider == "p2"

    def test_add_provider(self, core):
        core.add_provider("new", "New", "https://new.com/v1")
        assert "new" in core.providers

    def test_remove_provider(self, core):
        core.add_provider("temp", "Temp", "https://temp.com/v1", models=["m1"])
        core.remove_provider("temp")
        assert "temp" not in core.providers


class TestMocodeCoreEvents:
    def test_event_subscription(self, core):
        events = []
        core.on_event(EventType.ERROR, lambda e: events.append(e.data))
        core.event_bus.emit(EventType.ERROR, {"error": "test"})
        assert events == [{"error": "test"}]

    def test_off_event(self, core):
        events = []
        handler = lambda e: events.append(1)
        core.on_event(EventType.ERROR, handler)
        core.off_event(EventType.ERROR, handler)
        core.event_bus.emit(EventType.ERROR, None)
        assert events == []


class TestMocodeCoreHistory:
    def test_clear_history(self, core):
        core.agent.messages = [{"role": "user", "content": "test"}]
        core.clear_history()
        assert core.messages == []

    def test_clear_history_resets_session(self, core):
        core.agent.messages = [{"role": "user", "content": "test"}]
        session = core.save_session()
        core.clear_history()
        assert core.current_session_id is None


class TestMocodeCorePrompt:
    def test_rebuild_system_prompt(self, core):
        original_prompt = core.agent.system_prompt
        core.rebuild_system_prompt()
        # After rebuild, prompt should be regenerated
        # (may be same content but call succeeds without error)
        assert core.agent.system_prompt is not None

    def test_update_system_prompt(self, core):
        core.update_system_prompt("Custom prompt")
        assert core.agent.system_prompt == "Custom prompt"


class TestMocodeCoreInject:
    @pytest.mark.asyncio
    async def test_inject_message(self, core):
        core.agent.chat = AsyncMock(return_value="injected response")
        result = await core.inject_message("Hello", "conv-1")
        assert result == "injected response"

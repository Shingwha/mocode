"""Shared test fixtures"""

import pytest

from mocode.core.config import Config
from mocode.core.events import EventBus
from mocode.core.interrupt import InterruptToken
from mocode.tools.base import ToolRegistry


@pytest.fixture
def config():
    """In-memory Config, no file I/O"""
    return Config.load(data={
        "current": {"provider": "test", "model": "test-model"},
        "providers": {
            "test": {
                "name": "Test Provider",
                "base_url": "https://api.test.com/v1",
                "api_key": "test-key",
                "models": ["test-model"],
            }
        },
    })


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def interrupt_token():
    return InterruptToken()


@pytest.fixture(autouse=True)
def clean_tool_registry():
    """Clean global tool registry before and after each test"""
    ToolRegistry.unregister_all()
    yield
    ToolRegistry.unregister_all()

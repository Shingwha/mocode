"""Shared test fixtures for v0.2"""

import pytest

from mocode.config import Config
from mocode.event import EventBus
from mocode.interrupt import CancellationToken
from mocode.tool import ToolRegistry


@pytest.fixture
def config():
    """In-memory Config"""
    return Config.from_dict({
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
def cancel_token():
    return CancellationToken()


@pytest.fixture
def registry():
    """Fresh instance-scoped ToolRegistry"""
    return ToolRegistry()

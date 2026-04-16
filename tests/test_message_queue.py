"""MessageQueue tests"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from mocode.event import EventBus, EventType
from mocode.message_queue import MessageQueue


@pytest.fixture
def mock_chat_fn():
    return AsyncMock(return_value="response")


@pytest.fixture
def queue(mock_chat_fn, event_bus):
    return MessageQueue(
        chat_fn=mock_chat_fn,
        event_bus=event_bus,
    )


class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_inject_blocking(self, queue, mock_chat_fn):
        result = await queue.inject("hello")
        assert result == "response"
        mock_chat_fn.assert_called_once_with("hello", None)

    @pytest.mark.asyncio
    async def test_inject_emits_idle(self, queue, event_bus):
        events = []
        event_bus.on(EventType.AGENT_IDLE, lambda e: events.append(e.data))
        await queue.inject("hello")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_inject_with_conversation_id(self, queue, mock_chat_fn):
        await queue.inject("hello", conversation_id="conv-123")
        mock_chat_fn.assert_called_once_with("hello", "conv-123")

    @pytest.mark.asyncio
    async def test_processing_state(self, queue, event_bus):
        states = []

        async def slow_chat(msg, conv_id=None):
            states.append(queue.is_processing)
            await asyncio.sleep(0.05)
            return "done"

        queue._chat_fn = slow_chat
        await queue.inject("hello")
        assert states[0] is True
        assert not queue.is_processing

    @pytest.mark.asyncio
    async def test_mark_unsaved_called(self, event_bus):
        called = []

        def mark_fn():
            called.append(True)

        q = MessageQueue(
            chat_fn=AsyncMock(return_value="ok"),
            event_bus=event_bus,
            mark_unsaved_fn=mark_fn,
        )
        await q.inject("hello")
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_not_processing_initially(self, queue):
        assert not queue.is_processing

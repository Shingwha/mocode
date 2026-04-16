"""EventBus tests"""

import asyncio

import pytest

from mocode.event import EventBus, Event, EventType


def test_on_emit(event_bus):
    results = []
    event_bus.on(EventType.TEXT_COMPLETE, lambda e: results.append(e.data))
    event_bus.emit(EventType.TEXT_COMPLETE, {"content": "hello"})
    assert results == [{"content": "hello"}]


def test_off(event_bus):
    results = []
    handler = lambda e: results.append(e.data)
    event_bus.on(EventType.TEXT_COMPLETE, handler)
    event_bus.off(EventType.TEXT_COMPLETE, handler)
    event_bus.emit(EventType.TEXT_COMPLETE, {"content": "hello"})
    assert results == []


def test_priority(event_bus):
    results = []
    event_bus.on(EventType.TEXT_COMPLETE, lambda e: results.append("second"), priority=100)
    event_bus.on(EventType.TEXT_COMPLETE, lambda e: results.append("first"), priority=1)
    event_bus.emit(EventType.TEXT_COMPLETE, None)
    assert results == ["first", "second"]


def test_multiple_handlers(event_bus):
    results = []
    event_bus.on(EventType.TEXT_COMPLETE, lambda e: results.append("a"))
    event_bus.on(EventType.TEXT_COMPLETE, lambda e: results.append("b"))
    event_bus.emit(EventType.TEXT_COMPLETE, None)
    assert results == ["a", "b"]


def test_handler_exception(event_bus):
    results = []

    def bad_handler(e):
        raise RuntimeError("boom")

    event_bus.on(EventType.TEXT_COMPLETE, bad_handler)
    event_bus.on(EventType.TEXT_COMPLETE, lambda e: results.append("ok"))
    event_bus.emit(EventType.TEXT_COMPLETE, None)
    assert results == ["ok"]


@pytest.mark.asyncio
async def test_emit_async(event_bus):
    results = []

    async def async_handler(e):
        results.append(e.data)

    event_bus.on(EventType.TEXT_COMPLETE, async_handler)
    await event_bus.emit_async(EventType.TEXT_COMPLETE, {"content": "async"})
    assert results == [{"content": "async"}]


def test_clear(event_bus):
    results = []
    event_bus.on(EventType.TEXT_COMPLETE, lambda e: results.append(1))
    event_bus.clear()
    event_bus.emit(EventType.TEXT_COMPLETE, None)
    assert results == []


def test_event_dataclass():
    event = Event(type=EventType.ERROR, data={"msg": "fail"})
    assert event.type == EventType.ERROR
    assert event.data == {"msg": "fail"}


def test_emit_no_subscribers(event_bus):
    event_bus.emit(EventType.ERROR, None)

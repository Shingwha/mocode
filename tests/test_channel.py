"""The event channel — ordering, replay, fan-out and honest loss."""

from __future__ import annotations

import asyncio

import pytest

from mocode.core.channel import EventChannel
from mocode.core.events import Notice, TextDelta


def _deltas(n: int) -> list[Notice]:
    return [Notice(message=str(i)) for i in range(n)]


class TestPublishing:
    async def test_seq_counts_up_and_is_stamped_on_each_event(self):
        channel = EventChannel()
        events = _deltas(3)
        for i, event in enumerate(events):
            assert await channel.publish(event) == i + 1
        assert channel.seq == 3
        assert [e.seq for e in events] == [1, 2, 3]


class TestSubscriptions:
    async def test_every_subscriber_sees_every_event(self):
        channel = EventChannel()
        first = channel.subscribe()
        second = channel.subscribe()
        await channel.publish(Notice(message="hello"))

        assert (await first.get()).message == "hello"
        assert (await second.get()).message == "hello"

    async def test_since_replays_the_backlog_and_continues_live(self):
        channel = EventChannel()
        for event in _deltas(3):
            await channel.publish(event)

        sub = channel.subscribe(since=1)

        assert [e.message for e in [await sub.get(), await sub.get()]] == ["1", "2"]
        assert sub.dropped == 0
        # 回放接着直播：补看之后到的，不断档也不重复。
        await channel.publish(Notice(message="3"))
        assert (await sub.get()).message == "3"

        # 默认 since=0：订阅从"现在"起，之前的一律不补看。
        live = channel.subscribe()
        await channel.publish(Notice(message="new"))
        assert (await live.get()).message == "new"

    async def test_a_subscription_that_fell_behind_is_told_so(self):
        channel = EventChannel(replay=4)
        for event in _deltas(10):
            await channel.publish(event)

        sub = channel.subscribe(since=0)

        # The buffer only holds the newest four, and the gap is counted rather
        # than papered over — the reader can see it in `seq` too.
        assert [e.message for e in channel.history(since=0)] == ["6", "7", "8", "9"]
        assert sub.dropped == 6
        assert sub.lagging
        # history 就是缓冲区的视图：窗内按 seq 排序，窗外为空。
        windowed = EventChannel()
        for event in _deltas(3):
            await windowed.publish(event)
        assert [e.seq for e in windowed.history(since=1)] == [2, 3]
        assert windowed.history(since=3) == []

    async def test_a_slow_reader_never_holds_up_the_publisher(self):
        channel = EventChannel(backlog=2)
        sub = channel.subscribe()
        for event in _deltas(5):
            await channel.publish(event)

        # The newest two survive; the publisher was never awaited on.
        assert (await sub.get()).message == "3"
        assert (await sub.get()).message == "4"
        assert sub.dropped == 3


class TestInline:
    async def test_the_publisher_waits_for_an_inline_subscriber(self):
        channel = EventChannel()
        finished: list[str] = []

        async def record(event) -> None:
            # A genuine suspension — a hop to another thread — not a sleep:
            # the point is that publish() does not return before the
            # subscriber's work is complete.
            await asyncio.to_thread(lambda: None)
            finished.append(event.message)

        channel.inline(record)
        assert await channel.publish(Notice(message="a")) == 1
        assert await channel.publish(Notice(message="b")) == 2
        # 每次 publish 返回时，订阅者的活都已真正干完。
        assert finished == ["a", "b"]

    async def test_inline_delivery_precedes_the_buffered_one(self):
        """A hook sees the event before a reader can act on it."""
        channel = EventChannel()
        order: list[str] = []

        async def hook(event) -> None:
            order.append("inline")

        channel.inline(hook)
        sub = channel.subscribe()
        await channel.publish(Notice(message="a"))
        # The buffered copy is already waiting; the hook has already run.
        assert order == ["inline"]
        assert (await sub.get()).message == "a"

    async def test_a_raising_inline_subscriber_is_not_the_publishers_problem(self):
        channel = EventChannel()

        async def broken(event) -> None:
            raise RuntimeError("boom")

        unsubscribe = channel.inline(broken)
        assert await channel.publish(Notice(message="a")) == 1

        unsubscribe()
        assert await channel.publish(Notice(message="b")) == 2


class TestClosing:
    async def test_closing_ends_every_subscription(self):
        channel = EventChannel()
        sub = channel.subscribe()
        channel.close(reason="conversation closed")

        assert channel.closed
        with pytest.raises(StopAsyncIteration):
            await sub.__anext__()

        # 关闭之后再订阅：立刻结束，不用等一条永远不来的事件。
        assert await channel.subscribe().get() is None

    async def test_a_closed_channel_still_serves_its_history(self):
        channel = EventChannel()
        await channel.publish(Notice(message="last words"))
        channel.close()
        assert [e.message for e in channel.history()] == ["last words"]


class TestIteration:
    async def test_iterating_a_subscription_yields_events_in_order(self):
        channel = EventChannel()
        sub = channel.subscribe()

        for event in _deltas(3):
            await channel.publish(event)
        channel.close()

        assert [e.message async for e in sub] == ["0", "1", "2"]

    async def test_a_reader_can_stop_by_closing(self):
        channel = EventChannel()
        sub = channel.subscribe()
        await channel.publish(TextDelta(text="a"))

        assert (await sub.get()).text == "a"
        sub.close()
        assert await sub.get() is None

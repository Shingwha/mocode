"""The event channel — ordering, replay, fan-out and honest loss."""

from __future__ import annotations

import asyncio

import pytest

from mocode.core.channel import EventChannel
from mocode.core.events import Notice, TextDelta


def _deltas(n: int) -> list[Notice]:
    return [Notice(message=str(i)) for i in range(n)]


class TestPublishing:
    @pytest.mark.asyncio
    async def test_seq_is_monotonic_across_turns(self):
        channel = EventChannel()
        for i in range(3):
            assert await channel.publish(Notice(message=str(i))) == i + 1
        assert channel.seq == 3

    @pytest.mark.asyncio
    async def test_events_are_stamped_with_their_seq(self):
        channel = EventChannel()
        events = _deltas(3)
        for event in events:
            await channel.publish(event)
        assert [e.seq for e in events] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_publishing_without_readers_is_fine(self):
        channel = EventChannel()
        await channel.publish(Notice(message="nobody is listening"))
        assert channel.seq == 1


class TestSubscriptions:
    @pytest.mark.asyncio
    async def test_every_subscriber_sees_every_event(self):
        channel = EventChannel()
        first = channel.subscribe()
        second = channel.subscribe()
        await channel.publish(Notice(message="hello"))

        assert (await first.get()).message == "hello"
        assert (await second.get()).message == "hello"

    @pytest.mark.asyncio
    async def test_a_live_subscription_starts_from_now(self):
        channel = EventChannel()
        await channel.publish(Notice(message="old"))
        sub = channel.subscribe()
        await channel.publish(Notice(message="new"))

        assert (await sub.get()).message == "new"

    @pytest.mark.asyncio
    async def test_since_replays_the_backlog(self):
        channel = EventChannel()
        for event in _deltas(3):
            await channel.publish(event)

        sub = channel.subscribe(since=1)

        assert [e.message for e in [await sub.get(), await sub.get()]] == ["1", "2"]
        assert sub.dropped == 0

    @pytest.mark.asyncio
    async def test_replay_then_live_has_no_gap_and_no_repeat(self):
        channel = EventChannel()
        await channel.publish(Notice(message="a"))
        sub = channel.subscribe(since=0)
        await channel.publish(Notice(message="b"))

        assert [e.message for e in [await sub.get(), await sub.get()]] == ["a", "b"]

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_a_slow_reader_never_holds_up_the_publisher(self):
        channel = EventChannel(backlog=2)
        sub = channel.subscribe()
        for event in _deltas(5):
            await channel.publish(event)

        # The newest two survive; the publisher was never awaited on.
        assert (await sub.get()).message == "3"
        assert (await sub.get()).message == "4"
        assert sub.dropped == 3

    @pytest.mark.asyncio
    async def test_history_is_a_view_of_the_buffer(self):
        channel = EventChannel()
        for event in _deltas(3):
            await channel.publish(event)

        assert [e.seq for e in channel.history(since=1)] == [2, 3]
        assert channel.history(since=3) == []


class TestInline:
    @pytest.mark.asyncio
    async def test_the_publisher_waits_for_an_inline_subscriber(self):
        channel = EventChannel()
        seen: list[str] = []

        def _recorder():
            async def record(event) -> None:
                await asyncio.sleep(0)
                seen.append(event.message)

            return record

        channel.inline(_recorder())
        await channel.publish(Notice(message="a"))
        await channel.publish(Notice(message="b"))

        assert seen == ["a", "b"]

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_a_raising_inline_subscriber_is_not_the_publishers_problem(self):
        channel = EventChannel()

        async def broken(event) -> None:
            raise RuntimeError("boom")

        unsubscribe = channel.inline(broken)
        assert await channel.publish(Notice(message="a")) == 1

        unsubscribe()
        assert await channel.publish(Notice(message="b")) == 2


class TestClosing:
    @pytest.mark.asyncio
    async def test_closing_ends_every_subscription(self):
        channel = EventChannel()
        sub = channel.subscribe()
        channel.close(reason="conversation closed")

        assert channel.closed
        with pytest.raises(StopAsyncIteration):
            await sub.__anext__()

    @pytest.mark.asyncio
    async def test_subscribing_after_the_close_ends_immediately(self):
        channel = EventChannel()
        channel.close()
        assert await channel.subscribe().get() is None

    @pytest.mark.asyncio
    async def test_a_closed_channel_still_serves_its_history(self):
        channel = EventChannel()
        await channel.publish(Notice(message="last words"))
        channel.close()
        assert [e.message for e in channel.history()] == ["last words"]


class TestIteration:
    @pytest.mark.asyncio
    async def test_iterating_a_subscription_yields_events_in_order(self):
        channel = EventChannel()
        sub = channel.subscribe()

        for event in _deltas(3):
            await channel.publish(event)
        channel.close()

        assert [e.message async for e in sub] == ["0", "1", "2"]

    @pytest.mark.asyncio
    async def test_a_reader_can_stop_by_closing(self):
        channel = EventChannel()
        sub = channel.subscribe()
        await channel.publish(TextDelta(text="a"))

        assert (await sub.get()).text == "a"
        sub.close()
        assert await sub.get() is None

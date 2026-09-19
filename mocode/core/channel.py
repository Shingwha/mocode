"""EventChannel — the loop's output as a stream more than one thing can read.

A run does not belong to whoever started it. It publishes into a channel that
outlives the run and anyone watching it, which is what makes four things
possible at once:

* a second reader — a status page, a logger, a test — alongside the one that
  started the turn;
* a reconnecting reader, which asks for everything after the last ``seq`` it
  saw and gets the backlog from the replay buffer;
* a slow reader, which falls behind without slowing the model down;
* a message that has nothing to do with a run — a plugin saying something
  between turns, where there is no run to attach it to.

Two delivery policies, one publish path:

* **inline** (:meth:`EventChannel.inline`) — the publisher waits. This is what a
  hook is: it must see the event before the run moves on.
* **buffered** (:meth:`EventChannel.subscribe`) — nobody waits. The reader has a
  bounded backlog; if it falls behind, the oldest events are dropped and
  ``seq`` says so, because a gap is self-describing: re-read the state and
  resync instead of pretending the stream was complete.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .events import Event

_log = logging.getLogger(__name__)

#: How many events a channel keeps for replay.
REPLAY = 1000

#: How far one buffered subscriber may fall behind before it starts losing the
#: oldest events instead of holding the publisher back.
BACKLOG = 1000

_CLOSED = object()
_SKIP = object()

InlineCallback = Callable[["Event"], Awaitable[None]]


class Subscription:
    """One reader's view of a channel — ordered, bounded, and honest about loss.

    Use it as an async iterator. ``dropped`` counts what this reader missed;
    because every event carries the channel's monotonic ``seq``, a gap in the
    sequence says the same thing without trusting the counter.

    A view can be narrowed at the source: ``keep`` filters what this reader
    wants, ``ends`` says which event finishes it (used to scope a reader to one
    turn without the channel having to know what a turn is).
    """

    def __init__(
        self,
        *,
        since: int | None = None,
        backlog: int = BACKLOG,
        keep: Callable[["Event"], bool] | None = None,
        ends: Callable[["Event"], bool] | None = None,
    ):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=backlog)
        self._backlog = backlog
        self._keep = keep
        self._ends = ends
        self._done = False
        #: True once a gap has been cut into this reader's stream.
        self.dropped = 0
        self._since = since

    # ── Reading ────────────────────────────────────────────

    @property
    def lagging(self) -> bool:
        """Whether this reader has lost events (``seq`` will have gaps)."""
        return self.dropped > 0

    def pending(self) -> int:
        """Events waiting in the backlog, not yet taken."""
        return self._queue.qsize()

    async def get(self) -> "Event | None":
        """The next event this reader wants, or ``None`` when it is over."""
        while not self._done:
            item = await self._queue.get()
            accepted = self._accept(item)
            if accepted is _SKIP:
                continue
            return accepted
        return None

    def take(self) -> "Event | None":
        """The next event that is already waiting — never waits for one.

        For a reader that wants to draw what is queued and then get on with
        something else, like a REPL about to ask for the next line.
        """
        while not self._done:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
            accepted = self._accept(item)
            if accepted is _SKIP:
                continue
            return accepted
        return None

    def _accept(self, item: object) -> "Event | None | object":
        """What this reader makes of one queue item: an event, skip, or the end."""
        if item is _CLOSED:
            self._done = True
            return None
        if self._keep is not None and not self._keep(item):
            return _SKIP
        if self._ends is not None and self._ends(item):
            self._done = True
        return item

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> "Event":
        event = await self.get()
        if event is None:
            raise StopAsyncIteration
        return event

    def close(self) -> None:
        """Stop reading. The subscription ends at the caller's side."""
        self._done = True
        self._offer(_CLOSED)

    # ── Producer side (the channel's business) ─────────────

    def _fill(self, events: list["Event"]) -> None:
        """Seed the backlog with replay, keeping the newest when it overflows."""
        if len(events) > self._backlog:
            self.dropped += len(events) - self._backlog
            events = events[-self._backlog :]
        for event in events:
            self._queue.put_nowait(event)

    def _offer(self, event: object) -> None:
        """Hand an event over without ever blocking the publisher."""
        try:
            self._queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        # The reader is behind. Drop its oldest event and keep the newest, so
        # what it does see is recent rather than stale.
        try:
            self._queue.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover - impossible while full
            pass
        if event is not _CLOSED:
            self.dropped += 1
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - impossible after the drop
            pass

    def _end(self) -> None:
        self._offer(_CLOSED)


class EventChannel:
    """An ordered, replayable fan-out of events, with ``seq`` per conversation.

    One channel belongs to one conversation and outlives its runs: ``seq`` keeps
    counting across turns, so a reader that remembers a number can always ask
    for what it missed — including "everything since I was last here".

    Nothing here interprets an event. Attribution comes from which channel you
    subscribed to, and ordering from ``seq``.
    """

    def __init__(self, *, replay: int = REPLAY, backlog: int = BACKLOG):
        self._replay: deque = deque(maxlen=replay)
        self._subscribers: list[Subscription] = []
        self._inline: list[InlineCallback] = []
        self._seq = 0
        self._closed = False
        self._backlog = backlog

    # ── Queries ────────────────────────────────────────────

    @property
    def seq(self) -> int:
        """The highest ``seq`` published so far (0 before the first event)."""
        return self._seq

    @property
    def closed(self) -> bool:
        return self._closed

    def history(self, *, since: int = 0) -> list["Event"]:
        """What the replay buffer still holds after *since* (may be incomplete)."""
        return [e for e in self._replay if e.seq > since]

    # ── Publishing ─────────────────────────────────────────

    async def publish(self, event: "Event") -> int:
        """Stamp, hand out, and return the event's ``seq``.

        Buffered readers get it first (nobody waits for them), then inline
        readers are awaited in registration order. That order keeps a nested
        publish — a hook emitting its own event — from being seen before the
        event that caused it.
        """
        self._seq += 1
        event.seq = self._seq
        self._replay.append(event)

        for sub in self._subscribers:
            sub._offer(event)

        for callback in list(self._inline):
            try:
                await callback(event)
            except Exception:
                _log.warning("inline subscriber failed", exc_info=True)
        return self._seq

    def subscribe(
        self,
        *,
        since: int | None = None,
        keep: Callable[["Event"], bool] | None = None,
        ends: Callable[["Event"], bool] | None = None,
    ) -> Subscription:
        """A reader's view. ``since=None`` is live-only; ``since=seq`` replays.

        Replay comes from the ring buffer, so a consumer that was away longer
        than the buffer holds gets a gap in ``seq`` rather than a silent
        partial history — see :attr:`Subscription.dropped`.
        """
        if self._closed:
            sub = Subscription(
                since=since, backlog=self._backlog, keep=keep, ends=ends
            )
            sub._end()
            return sub

        sub = Subscription(since=since, backlog=self._backlog, keep=keep, ends=ends)
        if since is not None and since < self._seq:
            backlog = self.history(since=since)
            missing = self._replay[0].seq - 1 - since if self._replay else 0
            if missing > 0:
                sub.dropped += missing
            sub._fill(backlog)
        self._subscribers.append(sub)
        return sub

    def inline(self, callback: InlineCallback) -> Callable[[], None]:
        """Register a callback the publisher awaits. Returns the unsubscriber."""
        self._inline.append(callback)

        def unsubscribe() -> None:
            try:
                self._inline.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def close(self, *, reason: str = "") -> None:
        """End every subscription. Further events are ignored."""
        if self._closed:
            return
        self._closed = True
        if reason:
            _log.debug("channel closed: %s", reason)
        for sub in self._subscribers:
            sub._end()
        self._subscribers.clear()
        self._inline.clear()


__all__ = ["BACKLOG", "REPLAY", "EventChannel", "Subscription"]

"""Turn — one execution of the loop, as an object.

A turn belongs to the loop, not to whoever started it: a reader that goes away
does not stop it, and a reader that arrives late replays what it missed from
the channel's buffer. ``id`` is the ``run_id`` every event of this turn
carries.

A turn always ends with :class:`~mocode.core.events.RunFinished` — including
one that was cancelled, which says so in ``cancelled`` — or with
:class:`~mocode.core.events.RunFailed`. Nothing else ends a turn.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .channel import Subscription
from .events import Event, RunFailed, RunFinished
from .state import RunState

if TYPE_CHECKING:
    from .agent import AgentLoop


def _ends_run(event: Event) -> bool:
    """Whether *event* is the terminal event of its run."""
    return isinstance(event, (RunFinished, RunFailed))


class Turn:
    """One execution of the loop: addressable, watchable, cancellable."""

    def __init__(self, *, agent: "AgentLoop", id: str, first_seq: int, user_input: str | None = None):
        self.id = id
        #: Channel ``seq`` before this turn published anything — the default
        #: replay point for :meth:`subscribe`.
        self.first_seq = first_seq
        self.state = RunState()
        #: The exception a failed turn ended on, for a caller that wants to raise it.
        self.failure: BaseException | None = None
        #: Whether the turn was stopped rather than finished.
        self.cancelled = False
        self._agent = agent
        self._started = False
        self._stopped = False
        self._stop_requested = False
        self._task = asyncio.create_task(agent._drive(id, user_input))

    # ── Watching ───────────────────────────────────────────

    @property
    def done(self) -> bool:
        return self._task.done()

    def subscribe(self, *, since: int | None = None) -> Subscription:
        """This turn's events, from ``since`` (default: before it started).

        The view closes on the turn's terminal event. ``since`` older than the
        channel's buffer yields a gap in ``seq``, not a silently partial run.
        """
        return self._agent.channel.subscribe(
            since=self.first_seq if since is None else since,
            keep=self._is_mine,
            ends=_ends_run,
        )

    def _is_mine(self, event: Event) -> bool:
        return event.run_id == self.id

    async def wait(self) -> RunFinished | RunFailed:
        """Wait for the turn to end and return its terminal event.

        The wait is shielded: giving up on it — or being cancelled — leaves the
        turn running, because the turn belongs to the loop and not to whoever is
        watching. Use :meth:`cancel` to stop it.
        """
        return await asyncio.shield(self._task)

    def cancel(self) -> None:
        """Stop the turn. Idempotent: a finished or already-stopped turn ignores it.

        A stop asked for before the turn took its first step is recorded and
        delivered the moment it starts — a task cancelled before it runs any
        code at all could never report the ending its readers are waiting for.
        """
        if self._task.done() or self._stopped:
            return
        self._stopped = True
        if self._started:
            self._task.cancel()
        else:
            self._stop_requested = True

    def _begin(self) -> bool:
        """Called by the loop as the turn's first act. True = stop immediately."""
        self._started = True
        return self._stop_requested


__all__ = ["Turn"]

"""RunState's content soft limit — a bounded window, not an unbounded turn.

A turn that streams forever must not grow the snapshot forever: past
``content_limit`` the fold keeps the newest text and marks the elision at
the front. Under the limit, nothing changes — that is the contract.
"""

from __future__ import annotations

from mocode.core.events import RunFinished, TextDelta
from mocode.core.state import DONE, RunState


def _stream(state: RunState, *texts: str) -> None:
    for text in texts:
        state.apply(TextDelta(text))


class TestContentSoftLimit:
    def test_the_default_limit_is_two_hundred_thousand(self):
        assert RunState().content_limit == 200_000

    def test_under_the_limit_the_content_is_verbatim(self):
        state = RunState(content_limit=10)
        _stream(state, "abc", "def")
        assert state.content == "abcdef"

    def test_over_the_limit_keeps_a_bounded_marked_window(self):
        state = RunState(content_limit=10)
        _stream(state, "x" * 30)

        assert state.content.startswith("…[")
        assert "chars elided]" in state.content
        assert state.content.endswith("\nxxxxxxxxxx")
        assert len(state.content) <= 10 + len("…[30 chars elided]\n")

    def test_the_marker_counts_what_was_dropped(self):
        state = RunState(content_limit=10)
        _stream(state, "0123456789abcdefghijk")  # 21 characters, one overflow
        assert state.content.startswith("…[11 chars elided]\n")
        assert state.content.endswith("bcdefghijk")

    def test_a_second_elision_recounts_the_marker(self):
        """Documented drift: the count is derived from the trimmed string,
        which by then contains the first marker — a valve, not a ledger."""
        state = RunState(content_limit=10)
        _stream(state, "0123456789", "abcdefghij", "k")
        assert state.content.endswith("bcdefghijk")
        assert state.content.startswith("…[20 chars elided]\n")

    def test_repeated_overflow_stays_bounded(self):
        state = RunState(content_limit=10)
        for _ in range(100):
            _stream(state, "y" * 25)
        assert len(state.content) <= 10 + 40  # window + the widest marker
        assert state.content.endswith("yyyyyyyyyy")

    def test_zero_disables_the_ceiling(self):
        state = RunState(content_limit=0)
        _stream(state, "z" * 50_000)
        assert state.content == "z" * 50_000

    def test_the_rest_of_the_snapshot_is_unaffected(self):
        state = RunState(content_limit=4)
        _stream(state, "x" * 40)
        state.apply(RunFinished(content="the answer", iterations=1))

        assert state.status == DONE
        assert state.answer == "the answer"  # never elided — from the event
        assert state.to_dict()["content"] == state.content  # still a plain str

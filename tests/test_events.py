"""The event contract and the state it reduces to.

These are the two things an embedding application depends on, so they are
tested on their own — no loop, no provider.
"""

from __future__ import annotations

from mocode.core import events as ev
from mocode.core.provider import Usage
from mocode.core.state import DONE, FAILED, RUNNING, RunState

ALL_EVENTS = [
    ev.RunStarted(model="m", tools=["a", "b"]),
    ev.IterationStarted(iteration=2),
    ev.TextDelta(text="hi"),
    ev.ReasoningDelta(text="why"),
    ev.ToolCallStarted(call_id="c1", name="echo", args={"v": 1}),
    ev.ToolOutput(call_id="c1", text="out", stream="stderr"),
    ev.ToolCallFinished(call_id="c1", name="echo", status="ok", result="r", duration=1.5),
    ev.IterationFinished(iteration=2, usage=Usage(1, 2), stop_reason="stop"),
    ev.RunFinished(content="answer", usage=Usage(3, 4), iterations=2, tool_calls_made=1),
    ev.RunFailed(error="boom", kind="RuntimeError"),
    ev.Notice(message="hi", level="warn"),
]


class TestEventContract:
    def test_every_event_has_a_unique_type(self):
        types = [type(e).type for e in ALL_EVENTS]
        assert len(types) == len(set(types))
        assert all(t != "event" for t in types)

    def test_to_dict_is_plain_data(self):
        for event in ALL_EVENTS:
            data = event.to_dict()
            assert data["type"] == type(event).type
            assert set(data) >= {"type", "run_id", "seq"}
            assert all(
                isinstance(v, (str, int, float, bool, type(None), list, dict))
                for v in data.values()
            ), data

    def test_nested_values_are_flattened(self):
        data = ev.RunFinished(content="a", usage=Usage(3, 4)).to_dict()
        assert data["usage"] == {"prompt_tokens": 3, "completion_tokens": 4}

        data = ev.ToolCallStarted(call_id="c", name="n", args={"a": [1, {"b": 2}]}).to_dict()
        assert data["args"] == {"a": [1, {"b": 2}]}

    def test_run_identity_is_keyword_only(self):
        """Subclass fields stay positional so construction reads naturally."""
        event = ev.TextDelta("hello")
        assert event.text == "hello" and event.run_id == "" and event.seq == 0


class TestEventSummary:
    """A frontend shows ``summary()``, so an event describes itself."""

    def test_the_default_names_the_type_and_its_fields(self):
        assert ev.ToolCallFinished(name="bash", status="ok").summary() == "bash [ok]"
        assert ev.TextDelta(text="hi").summary() == "TextDelta: text='hi'"

    def test_the_envelope_is_left_out(self):
        summary = ev.Notice(message="hi").summary()
        assert "run_id" not in summary and "seq" not in summary

    def test_a_plugin_event_may_override_it(self):
        from dataclasses import dataclass

        @dataclass
        class Compacted(ev.Event):
            type = "compacted"
            before: int = 0
            after: int = 0

            def summary(self) -> str:
                return f"compacted {self.before} → {self.after}"

        assert Compacted(before=12, after=3).summary() == "compacted 12 → 3"


class TestRunState:
    def _state(self, *events) -> RunState:
        state = RunState()
        for event in events:
            state.apply(event)
        return state

    def test_run_lifecycle(self):
        state = self._state(
            ev.RunStarted(model="m", tools=["a"]),
            ev.TextDelta(text="think"),
            ev.RunFinished(content="answer", iterations=1),
        )
        assert (state.model, state.status) == ("m", DONE)
        assert state.content == "think"
        assert state.answer == "answer"

    def test_failure_is_recorded(self):
        state = self._state(ev.RunStarted(), ev.RunFailed(error="boom", kind="RuntimeError"))
        assert (state.status, state.error) == (FAILED, "boom")

    def test_usage_sums_across_iterations(self):
        state = self._state(
            ev.IterationFinished(iteration=1, usage=Usage(10, 5)),
            ev.IterationFinished(iteration=2, usage=Usage(3, 2)),
        )
        assert state.usage == Usage(13, 7)
        assert state.last_usage == Usage(3, 2)

    def test_a_tool_call_runs_from_started_to_finished(self):
        state = self._state(
            ev.RunStarted(),
            ev.ToolCallStarted(call_id="c1", name="bash", args={"command": "ls"}),
        )
        assert [c.name for c in state.running_tool_calls] == ["bash"]

        state.apply(ev.ToolCallFinished(call_id="c1", name="bash", status="ok", result="a\nb"))
        call = state.tool_calls["c1"]
        assert call.done and call.status == "ok" and call.result == "a\nb"
        assert state.running_tool_calls == []

    def test_tool_output_accumulates_per_call(self):
        state = self._state(
            ev.RunStarted(),
            ev.ToolCallStarted(call_id="c1", name="bash"),
            ev.ToolOutput(call_id="c1", text="one\n"),
            ev.ToolOutput(call_id="c1", text="two\n"),
            ev.ToolOutput(call_id="other", text="ignored"),
        )
        assert state.tool_calls["c1"].output_text == "one\ntwo\n"

    def test_failed_tool_calls_are_queryable(self):
        state = self._state(
            ev.RunStarted(),
            ev.ToolCallStarted(call_id="c1", name="a"),
            ev.ToolCallFinished(call_id="c1", name="a", status="timeout"),
            ev.ToolCallStarted(call_id="c2", name="b"),
            ev.ToolCallFinished(call_id="c2", name="b", status="ok"),
        )
        assert [c.name for c in state.failed_tool_calls] == ["a"]
        assert state.tool_calls_made == 2

    def test_unknown_events_are_ignored(self):
        class PluginEvent(ev.Event):
            type = "plugin_event"

        state = self._state(ev.RunStarted(), PluginEvent())
        assert state.status == RUNNING

    def test_an_overlapping_replay_does_not_double_count(self):
        """A reconnect that re-reads a range it had folded stays correct."""
        events = [
            self._stamped(ev.RunStarted(run_id="r1"), 1),
            self._stamped(ev.TextDelta(run_id="r1", text="think"), 2),
            self._stamped(ev.TextDelta(run_id="r1", text="ing"), 3),
            self._stamped(ev.RunFinished(run_id="r1", content="thinking"), 4),
        ]
        state = RunState()
        for event in events:
            state.apply(event)

        for event in events[1:]:  # the overlap, replayed
            state.apply(event)

        assert state.content == "thinking"
        assert state.status == DONE

    def test_unstamped_events_always_apply(self):
        """The loop folds an event before the channel stamps it: seq 0 is live."""
        state = RunState()
        state.apply(ev.RunStarted(run_id="r1"))
        state.apply(ev.TextDelta(text="a"))
        state.apply(ev.TextDelta(text="b"))
        assert state.content == "ab"

    def test_another_runs_events_do_not_fold_in(self):
        """One RunState is one run's view, even on a channel others share."""
        state = RunState()
        state.apply(self._stamped(ev.RunStarted(run_id="parent"), 1))
        state.apply(self._stamped(ev.TextDelta(run_id="child", text="noise"), 2))
        state.apply(self._stamped(ev.ToolCallStarted(run_id="child", call_id="c1"), 3))
        assert state.content == ""
        assert state.tool_calls == {}

    @staticmethod
    def _stamped(event: ev.Event, seq: int) -> ev.Event:
        event.seq = seq
        return event

    def test_to_dict_is_plain_data(self):
        state = self._state(
            ev.RunStarted(model="m"),
            ev.ToolCallStarted(call_id="c1", name="echo", args={"v": 1}),
            ev.ToolOutput(call_id="c1", text="chunk"),
            ev.ToolCallFinished(call_id="c1", name="echo", status="ok", result="done"),
            ev.RunFinished(content="a", usage=Usage(1, 2)),
        )
        data = state.to_dict()
        assert data["status"] == DONE
        assert data["tool_calls"]["c1"]["output"] == "chunk"
        assert data["usage"] == {"prompt_tokens": 1, "completion_tokens": 2}

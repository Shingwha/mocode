"""The event contract and the state it reduces to.

These are the two things an embedding application depends on, so they are
tested on their own — no loop, no provider. The fold's content is a
bounded window, not an unbounded turn: past ``content_limit`` the newest
text is kept and the elision marked at the front.
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
    ev.ToolCallArgsDelta(call_id="c1", name="write", arguments='{"pa'),
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
        # 信封（run_id / seq）不出现在事件对自己的描述里。
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

    def test_the_lifecycle_folds_its_ending(self):
        """Finished 收敛到 DONE 并留下答案；Failed 收敛到 FAILED 并留下错误。"""
        done = self._state(
            ev.RunStarted(model="m", tools=["a"]),
            ev.TextDelta(text="think"),
            ev.RunFinished(content="answer", iterations=1),
        )
        assert (done.model, done.status) == ("m", DONE)
        assert done.content == "think"
        assert done.answer == "answer"

        failed = self._state(
            ev.RunStarted(), ev.RunFailed(error="boom", kind="RuntimeError")
        )
        assert (failed.status, failed.error) == (FAILED, "boom")

    def test_usage_sums_across_iterations(self):
        state = self._state(
            ev.IterationFinished(iteration=1, usage=Usage(10, 5)),
            ev.IterationFinished(iteration=2, usage=Usage(3, 2)),
        )
        assert state.usage == Usage(13, 7)
        assert state.last_usage == Usage(3, 2)

    def test_a_tool_call_runs_from_started_to_finished(self):
        """一个调用的折叠全程：在飞、输出累积、终态，计数与调用数一致。"""
        state = self._state(
            ev.RunStarted(),
            ev.ToolCallStarted(call_id="c1", name="bash", args={"command": "ls"}),
        )
        assert [c.name for c in state.tool_calls.values() if not c.done] == ["bash"]

        # 输出按调用累积，别的调用的输出不混进来。
        state.apply(ev.ToolOutput(call_id="c1", text="one\n"))
        state.apply(ev.ToolOutput(call_id="c1", text="two\n"))
        state.apply(ev.ToolOutput(call_id="other", text="ignored"))
        assert state.tool_calls["c1"].output_text == "one\ntwo\n"

        state.apply(ev.ToolCallFinished(call_id="c1", name="bash", status="ok", result="a\nb"))
        call = state.tool_calls["c1"]
        assert call.done and call.status == "ok" and call.result == "a\nb"
        assert [c for c in state.tool_calls.values() if not c.done] == []

        # 终态逐调用折叠：每个调用各带自己的 status，计数与调用数一致。
        another = self._state(
            ev.RunStarted(),
            ev.ToolCallStarted(call_id="c1", name="a"),
            ev.ToolCallFinished(call_id="c1", name="a", status="timeout"),
            ev.ToolCallStarted(call_id="c2", name="b"),
            ev.ToolCallFinished(call_id="c2", name="b", status="ok"),
        )
        assert another.tool_calls["c1"].status == "timeout"
        assert another.tool_calls["c2"].status == "ok"
        assert another.tool_calls_made == 2

    def test_unknown_events_are_ignored(self):
        class PluginEvent(ev.Event):
            type = "plugin_event"

        state = self._state(ev.RunStarted(), PluginEvent())
        assert state.status == RUNNING

    def test_the_seq_guard_passes_live_events_and_dedups_replays(self):
        """seq 守卫的两面：没盖戳的（seq 0）总放行，盖了戳的重放不重复计。"""
        # 已盖戳的一段，中间又被重放一遍——内容不翻倍，收场不变。
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

        # The loop folds an event before the channel stamps it: seq 0 is live.
        live = RunState()
        live.apply(ev.RunStarted(run_id="r1"))
        live.apply(ev.TextDelta(text="a"))
        live.apply(ev.TextDelta(text="b"))
        assert live.content == "ab"

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


class TestContentSoftLimit:
    """RunState's content is a bounded window, not an unbounded turn.

    A turn that streams forever must not grow the snapshot forever: past
    ``content_limit`` the fold keeps the newest text and marks the elision
    at the front. Under the limit, nothing changes — that is the contract.
    """

    @staticmethod
    def _stream(state: RunState, *texts: str) -> None:
        for text in texts:
            state.apply(ev.TextDelta(text))

    def test_the_default_limit_is_two_hundred_thousand(self):
        assert RunState().content_limit == 200_000

    def test_under_the_limit_the_content_is_verbatim(self):
        state = RunState(content_limit=10)
        self._stream(state, "abc", "def")
        assert state.content == "abcdef"

    def test_over_the_limit_keeps_a_bounded_marked_window(self):
        state = RunState(content_limit=10)
        self._stream(state, "x" * 30)

        assert state.content.startswith("…[")
        assert "chars elided]" in state.content
        assert state.content.endswith("\nxxxxxxxxxx")
        assert len(state.content) <= 10 + len("…[30 chars elided]\n")

        # 标记数的是被丢掉的那段：21 个字符超窗 10，丢 11。
        counted = RunState(content_limit=10)
        self._stream(counted, "0123456789abcdefghijk")  # 21 characters, one overflow
        assert counted.content.startswith("…[11 chars elided]\n")
        assert counted.content.endswith("bcdefghijk")

    def test_a_second_elision_recounts_the_marker(self):
        """Documented drift: the count is derived from the trimmed string,
        which by then contains the first marker — a valve, not a ledger."""
        state = RunState(content_limit=10)
        self._stream(state, "0123456789", "abcdefghij", "k")
        assert state.content.endswith("bcdefghijk")
        assert state.content.startswith("…[20 chars elided]\n")

    def test_repeated_overflow_stays_bounded(self):
        state = RunState(content_limit=10)
        for _ in range(100):
            self._stream(state, "y" * 25)
        assert len(state.content) <= 10 + 40  # window + the widest marker
        assert state.content.endswith("yyyyyyyyyy")

    def test_zero_disables_the_ceiling(self):
        state = RunState(content_limit=0)
        self._stream(state, "z" * 50_000)
        assert state.content == "z" * 50_000

    def test_the_rest_of_the_snapshot_is_unaffected(self):
        state = RunState(content_limit=4)
        self._stream(state, "x" * 40)
        state.apply(ev.RunFinished(content="the answer", iterations=1))

        assert state.status == DONE
        assert state.answer == "the answer"  # never elided — from the event
        assert state.to_dict()["content"] == state.content  # still a plain str

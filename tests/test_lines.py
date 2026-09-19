"""The terminal vocabulary — what a turn looks like, as data.

No terminal involved: `lines.py` builders are pure, so these assert the shape
directly instead of scraping escape-laden output. If the vocabulary changes,
this is the file that should change with it.
"""

from __future__ import annotations

import pytest

from mocode.cli import lines
from mocode.cli.lines import Line
from mocode.core import Tool, ToolRegistry
from mocode.core.events import ToolCallFinished
from mocode.core.provider import Usage


def _registry(result_key: str = "lines") -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            "read", "d", {"path": {"type": "string", "description": "p"}},
            lambda a: "ok", summary_key="path", result_key=result_key,
        )
    )
    return registry


def _finished(status="ok", result="", details=None, duration=-1.0, name="read") -> ToolCallFinished:
    return ToolCallFinished(
        call_id="c1", name=name, status=status, result=result,
        details=details or {}, duration=duration,
    )


class TestConversation:
    """Everything starts at column 0; the first character says what a line is."""

    def test_the_user_line_carries_the_prompt_marker(self):
        line = lines.user("  帮我看看  ")
        assert (line.icon, line.text, line.style) == ("❯", "帮我看看", "user")

    def test_the_answer_is_unmarked(self):
        """It is the default state — the one thing a reader came for."""
        assert lines.answer("第一行\n第二行") == [Line(text="第一行"), Line(text="第二行")]

    def test_reasoning_is_dim_and_unmarked(self):
        assert lines.reasoning("先看看目录") == [
            Line(text="先看看目录", style="reasoning")
        ]

    def test_a_prompt_is_what_you_typed_plus_a_blank(self):
        assert lines.prompt("你好") == [lines.user("你好"), Line()]

    def test_the_rule_closes_a_turn(self):
        rule = lines.divider()
        assert set(rule.text) == {"─"}
        assert rule.style == "dim"

    def test_notice_levels_pick_a_colour(self):
        assert lines.notice("hi").style == "info"
        assert lines.notice("hi", "warn").style == "warning"
        assert lines.notice("hi", "error").style == "error"

    def test_the_turn_costs_one_line(self):
        line = lines.tokens(Usage(prompt_tokens=1234, completion_tokens=567))
        assert line == Line(text="↑1,234 ↓567 tokens", style="muted")


class TestToolLines:
    def test_a_silent_tool_is_a_single_line(self):
        """The verdict carries the identity — nothing else was ever drawn."""
        line = lines.tool_close(
            _finished(details={"lines": 412}), {"path": "a.py"}, _registry()
        )
        assert (line.icon, line.text) == ("✓", "read  a.py")
        assert line.note == "lines=412"

    def test_elapsed_joins_the_note(self):
        line = lines.tool_close(_finished(duration=1.25), {"path": "a.py"}, _registry())
        assert line.note == "1.2s"

    def test_a_fast_call_does_not_report_a_duration(self):
        line = lines.tool_close(_finished(duration=0.02), {"path": "a.py"}, _registry())
        assert line.note == ""

    def test_a_tool_that_declares_no_result_key_shows_no_detail(self):
        line = lines.tool_close(
            _finished(details={"lines": 412}), {"path": "a.py"}, _registry(result_key="")
        )
        assert line.note == ""

    def test_a_running_call_is_dim_and_names_itself(self):
        """It holds the row its verdict will land on, so it has to read alone."""
        line = lines.tool_pending("read", {"path": "a.py"}, _registry())
        assert (line.icon, line.text, line.style) == ("·", "read  a.py…", "dim")

    def test_a_running_call_without_arguments_is_still_a_line(self):
        assert lines.tool_pending("make", {}, _registry()).text == "make…"

    def test_a_long_argument_is_elided_in_the_middle(self):
        """Head and tail are what identify a path; the middle is what is droppable."""
        line = lines.tool_pending("read", {"path": "a/" + "b" * 200 + "/f.py"}, _registry())
        assert "..." in line.text
        assert line.text.startswith("read  a/") and line.text.endswith("/f.py…")
        assert len(line.text) < lines.SUMMARY_WIDTH + 10


class TestFailures:
    @pytest.mark.parametrize(
        "status,result,duration,expected",
        [
            ("error", "error: command not found", -1.0, "error: command not found"),
            ("denied", "denied: not allowed", -1.0, "denied (not allowed)"),
            ("timeout", "timeout: 5s", 5.0, "timed out after 5s"),
            ("not_found", "unknown tool 'ghost'", -1.0, "unknown tool 'ghost'"),
        ],
    )
    def test_the_phrase_explains_the_status(self, status, result, duration, expected):
        line = lines.tool_close(_finished(status, result, duration=duration), {}, _registry())
        assert line.icon == "✗"
        assert line.note == expected

    def test_a_timeout_does_not_repeat_its_own_duration(self):
        line = lines.tool_close(_finished("timeout", duration=5.0), {}, _registry())
        assert line.note == "timed out after 5s"


class TestReplay:
    """History goes through the same builders, so a resumed session reads the same."""

    @staticmethod
    def _messages() -> list[dict]:
        return [
            {"role": "user", "content": "ls 一下"},
            {
                "role": "assistant",
                "content": "看一下。",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "a.py"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
            {"role": "assistant", "content": "里面有 3 行。"},
        ]

    def test_a_turn_replays_in_order(self):
        got = lines.conversation(self._messages(), _registry())

        assert got[0] == lines.user("ls 一下")
        assert got[1] == Line()                        # the blank after a prompt
        assert got[2] == Line(text="看一下。")          # commentary, unmarked
        assert got[3].icon == "✓" and got[3].text == "read  a.py"
        assert got[-2] == Line(text="里面有 3 行。")
        assert set(got[-1].text) == {"─"}              # the turn's closing rule

    def test_each_turn_ends_with_a_rule(self):
        got = lines.conversation(
            self._messages() + [{"role": "user", "content": "再来"}], _registry()
        )
        rules = [line for line in got if line.text and set(line.text) == {"─"}]
        assert len(rules) == 1              # one turn has finished so far
        assert got[-2] == lines.user("再来")  # …then the next prompt, no rule of its own

    def test_a_failed_result_replays_as_a_failure(self):
        messages = self._messages()
        messages[2]["content"] = "error: command not found"

        verdict = lines.conversation(messages, _registry())[3]

        assert verdict.icon == "✗"
        assert verdict.note == "error: command not found"

    def test_a_timeout_prefix_replays_as_a_timeout(self):
        messages = self._messages()
        messages[2]["content"] = "timeout: 240s"

        assert "timed out" in lines.conversation(messages, _registry())[3].note

    def test_history_carries_no_durations(self):
        """Nothing stored a timing, so nothing invents one."""
        verdict = lines.conversation(self._messages(), _registry())[3]
        assert verdict.note == ""

    def test_multimodal_user_content_becomes_text(self):
        got = lines.conversation(
            [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:..."}},
                {"type": "text", "text": "这是什么"},
            ]}],
            _registry(),
        )
        assert "这是什么" in got[0].text


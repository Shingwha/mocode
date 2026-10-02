"""Display — putting lines on a terminal, and a turn as it is drawn.

What a turn *looks like* is asserted in `test_lines.py`; anything that needs a
terminal is here. The renderer is driven the way the CLI drives it: a
conversation's event stream in, lines out.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
import re

from mocode.cli import lines
from mocode.cli.display import Display
from mocode.cli.painter import Painter, fit_row, wrap_rows
from mocode.cli.render import CLIRenderer
from mocode.cli.theme import Theme
from mocode.cli.transcript import Transcript
from mocode.core import (
    AgentLoop,
    Event,
    HookRunner,
    Notice,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    TextDelta,
    Tool,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
    ToolRegistry,
    ToolResult,
)
from mocode.core.events import PluginMessage
from mocode.core.provider import Response, ToolCall, Usage
from mocode.testing import MockProvider, tool_call_response

from .conftest import strip_ansi


def _make_display(live: bool = False) -> Display:
    """A display for a test terminal.

    ``live`` is pinned rather than detected: whether stdout happens to be a real
    terminal must not decide what these assert.
    """
    return Display(input_=MagicMock(), theme=Theme(), live=live)


class _StubConversation:
    """What the renderer reads off a conversation: its tools, and its history."""

    def __init__(self, tools: ToolRegistry):
        self.tools = tools
        self.messages: list[dict] = []


def _renderer(display: Display, registry: ToolRegistry) -> CLIRenderer:
    return CLIRenderer(display, _StubConversation(registry))


def _plain(text: str) -> str:
    return strip_ansi(text)


class TestFormatting:
    def test_a_line_renders_its_icon_text_and_note(self):
        display = _make_display()
        got = display.format(
            lines.Line(
                text="read  a.py", icon="✓", icon_style="success",
                style="accent", note="lines=3",
            )
        )
        assert _plain(got) == "✓ read  a.py · lines=3"

    def test_an_answer_line_has_nothing_in_front_of_it(self):
        assert _plain(_make_display().format(lines.Line(text="hello"))) == "hello"

    def test_a_note_alone_does_not_leave_a_dangling_separator(self):
        got = _make_display().format(lines.Line(icon="✓", icon_style="success", note="lines=3"))
        assert _plain(got) == "✓ lines=3"

    def test_an_unknown_style_is_left_uncoloured(self):
        assert _plain(_make_display().format(lines.Line(text="x", style="nope"))) == "x"


class TestRowFitting:
    """A region row has to be exactly one terminal row, or its offsets lie."""

    def test_text_that_fits_is_left_alone(self):
        assert fit_row("read  a.py", 40) == "read  a.py"

    def test_an_overlong_line_ends_in_an_ellipsis(self):
        assert _plain(fit_row("x" * 100, 10)) == "x" * 9 + "…"

    def test_styling_survives_and_is_closed(self):
        got = fit_row("\033[2m" + "x" * 100 + "\033[0m", 10)
        assert got.startswith("\033[2m")
        assert got.endswith("\033[0m")
        assert _plain(got) == "x" * 9 + "…"

    def test_wide_characters_are_measured_not_counted(self):
        assert _plain(fit_row("中文测试宽度", 8)) == "中文测…"

    def test_wrap_rows_splits_at_the_width_without_losing_text(self):
        rows = wrap_rows("abcdefghij", 4)
        assert rows == ["abcd", "efgh", "ij"]

    def test_wrap_rows_measures_wide_characters(self):
        assert wrap_rows("中文中文中", 4) == ["中文", "中文", "中"]

    def test_wrap_rows_keeps_an_empty_line(self):
        assert wrap_rows("", 4) == [""]


class TestStreaming:
    def test_the_answer_streams_at_the_left_margin(self, capsys):
        display = _make_display()

        display.stream("Hel", kind="answer")
        display.stream("lo\nworld\n", kind="answer")
        display.end_stream()

        assert _plain(capsys.readouterr().out) == "Hello\nworld\n"

    def test_reasoning_streams_indented_without_a_glyph(self, capsys):
        display = _make_display()

        display.stream("先看看\n再说\n", kind="reasoning")
        display.end_stream()

        assert _plain(capsys.readouterr().out) == "先看看\n再说\n"

    def test_switching_kind_closes_the_open_block(self, capsys):
        display = _make_display()

        display.stream("thinking", kind="reasoning")
        display.stream("answer", kind="answer")
        display.end_stream()

        assert _plain(capsys.readouterr().out) == "thinking\nanswer\n"

    def test_a_line_output_closes_an_open_stream(self, capsys):
        display = _make_display()

        display.stream("partial", kind="answer")
        display.render(lines.Line(text="next line"))

        assert _plain(capsys.readouterr().out) == "partial\nnext line\n"

    def test_a_plugin_event_renders_its_own_summary(self, capsys):
        from dataclasses import dataclass

        @dataclass
        class Compacted(Event):
            type = "compacted"
            before: int = 0
            after: int = 0

            def summary(self) -> str:
                return f"compacted {self.before} → {self.after}"

        _make_display().render_event(Compacted(before=12, after=3))

        assert _plain(capsys.readouterr().out) == "compacted 12 → 3\n"


class TestMessages:
    def test_messages_carry_a_level(self, capsys):
        display = _make_display()
        display.info("plain")
        display.warn("careful")
        display.error("broken")
        assert _plain(capsys.readouterr().out).splitlines() == ["plain", "careful", "broken"]

    def test_a_replaced_conversation_is_replayed(self, capsys):
        display = _make_display()
        display.conversation_changed(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        )
        out = _plain(capsys.readouterr().out)
        assert "❯ hi" in out and "\nhello" in out
        assert set(out.splitlines()[-1]) == {"─"}   # the replayed turn is closed


class TestRenderer:
    """A whole turn, asserted through the renderer a CLI actually runs."""

    async def _run(self, provider, *tools: Tool, display=None):
        display = display or _make_display()
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        agent = AgentLoop(
            provider=provider,
            system_prompt="t",
            tools=registry,
            hooks=HookRunner(),
        )
        renderer = _renderer(display, registry)
        async for event in agent.stream("hi"):
            renderer.draw(event)
        return display

    @pytest.mark.asyncio
    async def test_text_and_a_silent_tool(self, capsys):
        await self._run(
            MockProvider([
                Response(
                    content="let me check", usage=Usage(1, 1), finish_reason="tool_calls",
                    tool_calls=tool_call_response("echo", '{"value":"x"}').tool_calls,
                ),
                Response(content="all done", usage=Usage(2, 2), finish_reason="stop"),
            ]),
            Tool("echo", "d", {"value": {"type": "string", "description": "v"}},
                 lambda a: "x", summary_key="value"),
        )

        rendered = _plain(capsys.readouterr().out).splitlines()
        assert rendered[:3] == [
            "let me check",   # the answer, unmarked
            "✓ echo  x",      # a silent tool: one line, verdict and identity together
            "all done",
        ]
        assert rendered[3] == "↑3 ↓3 tokens"   # what the turn cost, all iterations
        assert set(rendered[4]) == {"─"}       # the rule that closes the turn

    @pytest.mark.asyncio
    async def test_a_provider_that_reports_no_usage_gets_no_line(self, capsys):
        await self._run(
            MockProvider([
                Response(content="done", usage=Usage(0, 0), finish_reason="stop"),
            ]),
        )

        assert "tokens" not in _plain(capsys.readouterr().out)

    @pytest.mark.asyncio
    async def test_a_talkative_tool_is_still_one_line(self, capsys):
        """What a call printed is the model's to read, not the terminal's."""

        async def chatty(args, ctx):
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text="building\n"))
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text="linking\n"))
            return "building\nlinking"

        await self._run(
            MockProvider([
                tool_call_response("make"),
                Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            Tool("make", "d", {}, chatty, with_context=True),
        )

        out = _plain(capsys.readouterr().out)
        assert [line for line in out.splitlines() if line[:1] in "·✓✗"] == ["✓ make"]
        assert "building" not in out

    @pytest.mark.asyncio
    async def test_a_result_detail_joins_the_verdict(self, capsys):
        await self._run(
            MockProvider([
                tool_call_response("run", '{"cmd": "ls"}'),
                Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            Tool("run", "d", {"cmd": {"type": "string", "description": "c"}},
                 lambda a: ToolResult("out", {"exit_code": 3}),
                 summary_key="cmd", result_key="exit_code"),
        )

        assert "✓ run  ls · exit_code=3" in _plain(capsys.readouterr().out)

    @pytest.mark.asyncio
    async def test_a_denied_call_is_a_failure(self, capsys):
        from mocode.core import AgentHook, ToolCallContext

        class Denier(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                ctx.deny = "not allowed"

        display = _make_display()
        registry = ToolRegistry()
        registry.register(Tool("risky", "d", {}, lambda a: "ran"))
        agent = AgentLoop(
            provider=MockProvider([
                tool_call_response("risky"),
                Response(content="ok", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            system_prompt="t",
            tools=registry,
            hooks=HookRunner([Denier()]),
        )
        renderer = _renderer(display, registry)
        async for event in agent.stream("hi"):
            renderer.draw(event)

        out = _plain(capsys.readouterr().out)
        assert "✗ risky · denied (not allowed)" in out


async def _run_parallel(display: Display, delays: dict[str, float]):
    """One batch of parallel tool calls, each finishing on its own delay."""

    async def slow(args, ctx):
        await asyncio.sleep(delays[args["tag"]])
        return args["tag"]

    schema = {
        "type": "object",
        "properties": {"tag": {"type": "string", "description": "t"}},
        "required": ["tag"],
    }
    registry = ToolRegistry()
    for name, delay in delays.items():
        registry.register(Tool(name, "d", schema, slow, summary_key="tag", with_context=True))
    provider = MockProvider([
        Response(
            tool_calls=[
                ToolCall(id=f"c{i}", name=name, arguments=f'{{"tag": "{name}"}}')
                for i, name in enumerate(delays)
            ],
            usage=Usage(1, 1),
            finish_reason="tool_calls",
        ),
        Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
    ])
    agent = AgentLoop(
        provider=provider,
        system_prompt="t",
        tools=registry,
        hooks=HookRunner(),
    )
    renderer = _renderer(display, registry)
    async for event in agent.stream("hi"):
        renderer.draw(event)


#: One region repaint opens by moving up to the top of its rewritable rows.
UP = re.compile(r"\x1b\[(\d+)A")
#: A row rewrite: clear the line, write the row, step to the next one.
ROW = re.compile(r"\r\x1b\[K([^\n]*)\n")


class TestLiveBlock:
    """A batch keeps one row per call — in call order, rewritten in place.

    On a terminal the row a call claims while it runs is the row its verdict
    lands on. The repaint's escape sequences are asserted exactly, because
    the row offsets are the whole mechanism: an offset one row off corrupts
    the screen.
    """

    @pytest.mark.asyncio
    async def test_a_batch_keeps_one_row_per_call_in_call_order(self, capsys):
        # 'a' finishes first and 'c' last — the rows must not follow that.
        await _run_parallel(_make_display(live=True), {"a": 0.01, "b": 0.02, "c": 0.03})
        out = capsys.readouterr().out

        # Every call claims its row before any of them finishes: the three
        # placeholders appear, in call order, as the region grows to three.
        assert list(dict.fromkeys(l for l in _plain(out).splitlines() if l.startswith("· "))) == [
            "· a  a…",
            "· b  b…",
            "· c  c…",
        ]
        assert UP.search(out).group(1) == "1"          # one row when 'b' joins
        assert out.count("\x1b[3A") >= 1               # three rows once 'c' has
        assert "\x1b[M" not in out                     # a batch only grows
        # Each verdict lands in its own row, in call order — the last state
        # the screen holds has all three, top to bottom, as claimed.
        assert [l for l in _plain(out).splitlines() if l.startswith("✓")][-3:] == [
            "✓ a  a",
            "✓ b  b",
            "✓ c  c",
        ]

    def test_a_plugin_message_draws_its_summary(self, capsys):
        """No drawer registered: the event still says itself, in one line."""
        renderer = _renderer(_make_display(), ToolRegistry())

        renderer.draw(
            PluginMessage(kind="shell/background-done", data={"jobs": [{"id": "s1"}]})
        )

        assert _plain(capsys.readouterr().out) == "plugin message: shell/background-done\n"


class TestPainterGolden:
    """The live region's ANSI, locked byte for byte.

    The escape sequence is the whole mechanism — an offset one row off
    corrupts the screen — so one small batch is pinned exactly, not just
    through the REWRITE pattern above.
    """

    def test_a_live_batch_writes_exact_bytes(self, capsys):
        display = _make_display(live=True)
        painter = Painter(display)
        transcript = Transcript()
        for event in (
            ToolCallStarted(call_id="a", name="read", args={"path": "x"}),
            ToolCallStarted(call_id="b", name="read", args={"path": "y"}),
            ToolCallFinished(call_id="a", name="read"),
            ToolCallFinished(call_id="b", name="read"),
        ):
            transcript.apply(event)
            painter.paint(transcript)

        A = "\x1b[2m·\x1b[0m \x1b[2mread  x…\x1b[0m"
        B = "\x1b[2m·\x1b[0m \x1b[2mread  y…\x1b[0m"
        VA = "\x1b[92m✓\x1b[0m \x1b[96mread  x\x1b[0m"
        VB = "\x1b[92m✓\x1b[0m \x1b[96mread  y\x1b[0m"
        assert capsys.readouterr().out == (
            # 'a' claims the region's first row
            f"{A}\n"
            # 'b' joins: up to the region top, rewrite, grow by one row
            f"\x1b[1A\r\x1b[K{A}\n{B}\n"
            # 'a' lands: the whole region repaints, verdict over its row
            f"\x1b[2A\r\x1b[K{VA}\n\r\x1b[K{B}\n"
            # 'b' lands: 'a' is final now, so only 'b's row is rewritten
            f"\x1b[1A\r\x1b[K{VB}\n"
        )

    def test_a_redirected_painter_appends_the_verdict_only(self, capsys):
        display = _make_display(live=False)
        painter = Painter(display)
        transcript = Transcript()
        for event in (
            ToolCallStarted(call_id="a", name="read", args={"path": "x"}),
            ToolCallFinished(call_id="a", name="read"),
        ):
            transcript.apply(event)
            painter.paint(transcript)

        out = capsys.readouterr().out
        assert _plain(out) == "✓ read  x\n"     # no placeholder row in a log
        assert "\x1b[" not in _plain(out)

    def test_a_live_stream_rewrites_the_unfinished_line(self, capsys, monkeypatch):
        """A finished line appends; the line still being written is rewritten."""
        monkeypatch.setattr("mocode.cli.painter.THROTTLE", 0)  # one write per delta
        display = _make_display(live=True)
        painter = Painter(display)
        transcript = Transcript()
        for event in (
            TextDelta(text="Hel"),
            TextDelta(text="lo\n"),
            TextDelta(text="world"),
            RunFinished(usage=Usage(4, 4)),
        ):
            transcript.apply(event)
            painter.paint(transcript, event)

        assert capsys.readouterr().out == (
            # the unfinished line is the region's row, left unterminated so
            # the next fragment continues it
            "Hel"
            # the line finishes: what is new appends — a committed line is
            # never rewritten — and the row it owned commits with it
            "lo\n"
            # the next unfinished line, again the region's row
            "world"
            # the turn closes: the line ends, and the rule lands below the
            # region rather than being lost to it
            "\n"
            "\x1b[90m↑4 ↓4 tokens\x1b[0m\n"
            "\x1b[2m" + "─" * 72 + "\x1b[0m\n"
        )

    def test_a_wrapping_line_counts_visual_rows(self, capsys, monkeypatch):
        """A partial line that wraps is split — one offset off corrupts the screen."""
        monkeypatch.setattr("mocode.cli.painter.THROTTLE", 0)
        monkeypatch.setattr("mocode.cli.painter.terminal_width", lambda: 20)
        display = _make_display(live=True)
        painter = Painter(display)
        transcript = Transcript()
        for event in (
            TextDelta(text="0123456789"),
            TextDelta(text="abcdefghij"),
            TextDelta(text="klm"),
        ):
            transcript.apply(event)
            painter.paint(transcript, event)

        assert capsys.readouterr().out == (
            "0123456789"
            # the line fills the row: one visual row, rewritten in place
            "\x1b[1A\r\x1b[K0123456789abcdefghij"
            # and overflows it: two visual rows, so the rewrite moves up one
            # and writes the second row below
            "\x1b[1A\r\x1b[K0123456789abcdefghij\nklm"
        )

    def test_a_sealed_stream_rides_in_the_region_below_the_next_tool(
        self, capsys, monkeypatch
    ):
        """Reasoning and answer are region members in turn — one each, in order."""
        monkeypatch.setattr("mocode.cli.painter.THROTTLE", 0)
        display = _make_display(live=True)
        painter = Painter(display)
        transcript = Transcript()
        R = "\x1b[90mthink\x1b[0m"
        A = "\x1b[2m·\x1b[0m \x1b[2mread  x…\x1b[0m"
        VA = "\x1b[92m✓\x1b[0m \x1b[96mread  x\x1b[0m"
        for event in (
            ReasoningDelta(text="think"),
            TextDelta(text="ans"),
            ToolCallStarted(call_id="a", name="read", args={"path": "x"}),
            ToolCallFinished(call_id="a", name="read"),
            RunFinished(usage=Usage(1, 1)),
        ):
            transcript.apply(event)
            painter.paint(transcript, event)

        assert capsys.readouterr().out == (
            # reasoning's unfinished line: the region's first row
            f"{R}"
            # the answer opens: reasoning's line ends and rides as a final row
            f"\n"
            f"ans"
            # the call starts: the answer's line ends and rides, and the
            # call's row grows below both
            f"\n{A}\n"
            # the verdict lands over the row the call claimed
            f"\x1b[1A\r\x1b[K{VA}\n"
            # and the turn closes below the region
            "\x1b[90m↑1 ↓1 tokens\x1b[0m\n"
            "\x1b[2m" + "─" * 72 + "\x1b[0m\n"
        )

    def test_a_window_coalesces_deltas_and_other_output_flushes_first(
        self, capsys, monkeypatch
    ):
        """Fragments inside the window are one write; a call takes the stage next."""
        monkeypatch.setattr("mocode.cli.painter.THROTTLE", 3600)  # never closes
        display = _make_display(live=True)
        painter = Painter(display)
        transcript = Transcript()
        A = "\x1b[2m·\x1b[0m \x1b[2mread  x…\x1b[0m"
        VA = "\x1b[92m✓\x1b[0m \x1b[96mread  x\x1b[0m"
        for event in (
            TextDelta(text="Hel"),
            TextDelta(text="lo world"),
            ToolCallStarted(call_id="a", name="read", args={"path": "x"}),
            ToolCallFinished(call_id="a", name="read"),
            RunFinished(usage=Usage(1, 1)),
        ):
            transcript.apply(event)
            painter.paint(transcript, event)

        assert capsys.readouterr().out == (
            # the first fragment: nothing has been written yet
            "Hel"
            # the second is coalesced — until the call starts, which flushes
            # the stream first: the row is rewritten with both fragments
            "\x1b[1A\r\x1b[KHello world"
            # the line ends with the stream, and the call's row grows below
            f"\n{A}\n"
            f"\x1b[1A\r\x1b[K{VA}\n"
            "\x1b[90m↑1 ↓1 tokens\x1b[0m\n"
            "\x1b[2m" + "─" * 72 + "\x1b[0m\n"
        )

    @pytest.mark.asyncio
    async def test_output_that_is_not_a_verdict_freezes_the_block(self, capsys):
        """A region is only rewritable while nothing else has been printed."""

        async def noisy(args, ctx):
            await ctx.emit(Notice(message="careful", level="warn"))
            return "x"

        display = _make_display(live=True)
        registry = ToolRegistry()
        registry.register(Tool("noisy", "d", {}, noisy, with_context=True))
        agent = AgentLoop(
            provider=MockProvider([
                tool_call_response("noisy"),
                Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            system_prompt="t",
            tools=registry,
            hooks=HookRunner(),
        )
        renderer = _renderer(display, registry)
        async for event in agent.stream("hi"):
            renderer.draw(event)

        out = capsys.readouterr().out
        assert "· noisy…" in _plain(out)          # the row it claimed
        assert "careful" in _plain(out)           # what committed the region
        after = out[out.index("careful"):]
        assert not UP.search(after)               # nothing is rewritten past it
        assert "✓ noisy" in _plain(out)           # so the verdict is appended

    @pytest.mark.asyncio
    async def test_a_redirected_run_prints_no_placeholders(self, capsys):
        """A log cannot be rewritten, and would keep the dim rows forever."""
        await _run_parallel(_make_display(live=False), {"a": 0.01, "b": 0.02, "c": 0.03})

        out = _plain(capsys.readouterr().out)
        assert not any(line.startswith("· ") for line in out.splitlines())
        assert {line for line in out.splitlines() if line.startswith("✓")} == {
            "✓ a  a", "✓ b  b", "✓ c  c",
        }

    @pytest.mark.asyncio
    async def test_a_region_taller_than_the_screen_stops_admitting_members(
        self, capsys, monkeypatch
    ):
        """Rows above the fold have scrolled away; their offsets mean nothing."""
        monkeypatch.setattr("mocode.cli.painter.terminal_height", lambda: 3)

        await _run_parallel(_make_display(live=True), {"a": 0.01, "b": 0.02, "c": 0.03})

        out = capsys.readouterr().out
        # Height 3 admits one row (the cap leaves room for the rest line):
        # 'a' claims it; 'b' and 'c' are refused and their verdicts append.
        assert len([l for l in _plain(out).splitlines() if l.startswith("· ")]) == 1
        assert "✓ c  c" in _plain(out)   # the refused calls append when they end


class TestTranscript:
    """The same event stream, folded into a document — no terminal involved."""

    @staticmethod
    def _registry() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            Tool("echo", "d", {"value": {"type": "string", "description": "v"}},
                 lambda a: "x", summary_key="value"),
        )
        return registry

    async def _events(self, provider, *tools: Tool):
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        agent = AgentLoop(
            provider=provider,
            system_prompt="t",
            tools=registry,
            hooks=HookRunner(),
        )
        return registry, [event async for event in agent.stream("hi")]

    @staticmethod
    def _messages() -> list[dict]:
        return [
            {"role": "user", "content": "ls 一下"},
            {
                "role": "assistant",
                "content": "看一下。",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"value": "x"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "x"},
            {"role": "assistant", "content": "里面有 3 行。"},
        ]

    @pytest.mark.asyncio
    async def test_a_turn_folds_into_blocks(self):
        """The shapes the renderer drew, as blocks: prose, the call, the rule."""
        registry = ToolRegistry()
        registry.register(
            Tool("echo", "d", {"value": {"type": "string", "description": "v"}},
                 lambda a: "x", summary_key="value")
        )
        _, events = await self._events(
            MockProvider([
                Response(
                    content="let me check", usage=Usage(1, 1), finish_reason="tool_calls",
                    tool_calls=tool_call_response("echo", '{"value":"x"}').tool_calls,
                ),
                Response(content="all done", usage=Usage(2, 2), finish_reason="stop"),
            ]),
            registry.get("echo"),
        )

        transcript = Transcript(registry)
        for event in events:
            transcript.apply(event)

        assert [b.kind for b in transcript.blocks] == [
            "answer", "tool", "answer", "rule",
        ]
        assert transcript.blocks[0].lines == [lines.Line(text="let me check")]
        tool_block = transcript.blocks[1]
        assert tool_block.state == "done"
        assert tool_block.lines[0].icon == "✓" and tool_block.lines[0].text == "echo  x"
        assert tool_block.meta["args"] == {"value": "x"}
        assert transcript.blocks[3].lines[0].text == "↑3 ↓3 tokens"
        assert set(transcript.blocks[3].lines[1].text) == {"─"}

        # A pure fold: replaying the same events builds the same document.
        replay = Transcript(registry)
        for event in events:
            replay.apply(event)
        assert replay.blocks == transcript.blocks

    @pytest.mark.asyncio
    async def test_a_tools_live_output_is_kept_with_its_call(self):
        async def chatty(args, ctx):
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text="building\n"))
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text="linking\n"))
            return "building\nlinking"

        _, events = await self._events(
            MockProvider([
                tool_call_response("make"),
                Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            Tool("make", "d", {}, chatty, with_context=True),
        )

        transcript = Transcript()
        for event in events:
            transcript.apply(event)

        block = next(b for b in transcript.blocks if b.kind == "tool")
        assert block.meta["output"] == "building\nlinking\n"   # kept, not drawn
        assert len(block.lines) == 1                            # one line, as ever

    def test_deltas_extend_one_streaming_block_per_kind(self):
        transcript = Transcript()
        transcript.apply(ReasoningDelta(text="think"))
        transcript.apply(TextDelta(text="thus"))

        assert [b.kind for b in transcript.blocks] == ["reasoning", "answer"]
        assert transcript.blocks[0].state == "done"
        assert transcript.blocks[0].lines == [lines.Line(text="think", style="reasoning")]
        assert transcript.blocks[1].state == "streaming"
        assert transcript.blocks[1].meta["text"] == "thus"

    def test_a_failed_turn_closes_with_the_error_and_the_rule(self):
        transcript = Transcript()
        transcript.apply(RunFailed(error="boom", kind="ValueError"))

        block = transcript.blocks[-1]
        assert block.kind == "rule"
        assert block.lines[0] == lines.notice("ValueError: boom", "error")
        assert set(block.lines[1].text) == {"─"}

    def test_plugin_messages_without_a_block_id_are_blocks_of_their_own(self):
        transcript = Transcript()
        transcript.apply(PluginMessage(kind="shell/background-done", data={"jobs": []}))
        transcript.apply(PluginMessage(kind="shell/background-done", data={"jobs": [1]}))

        assert [b.kind for b in transcript.blocks] == ["plugin", "plugin"]
        # No drawer was registered, so the event describes itself — one line.
        assert transcript.blocks[0].lines == [
            lines.notice("plugin message: shell/background-done", "info")
        ]

    def test_one_block_id_folds_updates_into_one_block(self):
        transcript = Transcript()
        transcript.apply(PluginMessage(kind="rag/index", data={"done": 1}, block_id="rag"))
        transcript.apply(PluginMessage(kind="rag/index", data={"done": 2}, block_id="rag"))
        transcript.apply(PluginMessage(block_id="rag", sealed=True))

        assert len(transcript.blocks) == 1
        block = transcript.blocks[0]
        assert block.state == "done"
        assert block.meta["data"] == {"done": 2}

    def test_an_update_after_the_seal_follows_the_block(self):
        transcript = Transcript()
        transcript.apply(PluginMessage(kind="shell/bg", data={"id": 1}, block_id="j1"))
        transcript.apply(PluginMessage(block_id="j1", sealed=True))
        transcript.apply(
            PluginMessage(kind="shell/bg", data={"id": 1, "exit": 0}, block_id="j1")
        )

        block = transcript.blocks[0]
        assert len(block.lines) == 2                      # the block, then its follow-up
        assert block.meta["follow-ups"] == [{"id": 1, "exit": 0}]

    def test_a_registered_drawer_decides_a_message_lines(self):
        class Table:
            def lines_for(self, event):
                return [lines.Line(text=f"rag {event.data['done']}/40")]

        transcript = Transcript(drawers=Table())
        transcript.apply(PluginMessage(kind="rag/index", data={"done": 12}))

        assert transcript.blocks[0].lines[0].text == "rag 12/40"

    def test_an_unknown_event_becomes_a_notice_of_its_summary(self):
        from dataclasses import dataclass

        @dataclass
        class Compacted(Event):
            type = "compacted"
            before: int = 0
            after: int = 0

            def summary(self) -> str:
                return f"compacted {self.before} → {self.after}"

        transcript = Transcript()
        transcript.apply(Compacted(before=12, after=3))

        assert transcript.blocks[0].kind == "notice"
        assert transcript.blocks[0].lines == [lines.notice("compacted 12 → 3", "info")]

    def test_history_folds_into_blocks_in_turn_shape(self):
        transcript = Transcript(self._registry())
        transcript.apply_history(self._messages(), self._registry())

        assert [b.kind for b in transcript.blocks] == [
            "user", "answer", "tool", "answer", "rule",
        ]
        assert transcript.blocks[0].lines == lines.prompt("ls 一下")
        assert transcript.blocks[2].lines[0].icon == "✓"

    def test_history_flattens_to_what_the_flat_replay_drew(self):
        """The block fold and ``L.conversation`` are one vocabulary, not two."""
        registry = self._registry()

        transcript = Transcript(registry)
        transcript.apply_history(self._messages(), registry)
        flattened = [line for block in transcript.blocks for line in block.lines]

        assert flattened == lines.conversation(self._messages(), registry)

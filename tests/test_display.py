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
from mocode.cli.display import Display, clamp_visible
from mocode.cli.render import CLIRenderer
from mocode.cli.theme import Theme
from mocode.core import (
    AgentLoop,
    Event,
    HookRunner,
    Notice,
    Tool,
    ToolOutput,
    ToolRegistry,
    ToolResult,
)
from mocode.core.provider import Response, ToolCall, Usage

from .conftest import strip_ansi
from .providers import MockProvider, tool_call_response


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


class TestClamping:
    """A block row has to be exactly one terminal row, or its offsets lie."""

    def test_text_that_fits_is_left_alone(self):
        assert clamp_visible("read  a.py", 40) == "read  a.py"

    def test_an_overlong_line_ends_in_an_ellipsis(self):
        assert _plain(clamp_visible("x" * 100, 10)) == "x" * 9 + "…"

    def test_styling_survives_and_is_closed(self):
        got = clamp_visible("\033[2m" + "x" * 100 + "\033[0m", 10)
        assert got.startswith("\033[2m")
        assert got.endswith("\033[0m")
        assert _plain(got) == "x" * 9 + "…"

    def test_wide_characters_are_measured_not_counted(self):
        assert _plain(clamp_visible("中文测试宽度", 8)) == "中文测…"


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
            Tool("make", "d", {}, chatty),
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

    params = {"tag": {"type": "string", "description": "t"}}
    registry = ToolRegistry()
    for name, delay in delays.items():
        registry.register(Tool(name, "d", params, slow, summary_key="tag"))
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


#: One in-place rewrite: move up n rows, clear the row, write the line, come
#: back down the same n — the backreference is half the assertion.
REWRITE = re.compile(r"\x1b\[(\d+)A\x1b\[K(.*?)\x1b\[\1B\r")


class TestLiveBlock:
    """A batch keeps one row per call — in call order, rewritten in place.

    On a terminal the row a call claims while it runs is the row its verdict
    lands on. These assert the escape sequence exactly, because the row offsets
    are the whole mechanism: an offset one row off corrupts the screen.
    """

    @pytest.mark.asyncio
    async def test_a_batch_keeps_one_row_per_call_in_call_order(self, capsys):
        # 'a' finishes first and 'c' last — the rows must not follow that.
        await _run_parallel(_make_display(live=True), {"a": 0.01, "b": 0.02, "c": 0.03})
        out = capsys.readouterr().out

        # Every call claims its row before any of them finishes: that is the
        # property the whole design rests on.
        assert [l for l in _plain(out).splitlines() if l.startswith("· ")] == [
            "· a  a…",
            "· b  b…",
            "· c  c…",
        ]
        # Then each verdict is written into its own row, counting up from the
        # bottom of the block.
        assert [(m[0], _plain(m[1])) for m in REWRITE.findall(out)] == [
            ("3", "✓ a  a"),
            ("2", "✓ b  b"),
            ("1", "✓ c  c"),
        ]

    @pytest.mark.asyncio
    async def test_output_that_is_not_a_verdict_freezes_the_block(self, capsys):
        """A row is only rewritable while nothing else has been printed."""

        async def noisy(args, ctx):
            await ctx.emit(Notice(message="careful", level="warn"))
            return "x"

        display = _make_display(live=True)
        registry = ToolRegistry()
        registry.register(Tool("noisy", "d", {}, noisy))
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
        assert "careful" in _plain(out)           # what froze the block
        assert not REWRITE.search(out)            # so the verdict is appended
        assert "✓ noisy" in _plain(out)

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
    async def test_a_block_taller_than_the_screen_stops_claiming_rows(
        self, capsys, monkeypatch
    ):
        """Rows above the fold have scrolled away; their offsets mean nothing."""
        monkeypatch.setattr("mocode.cli.display.terminal_height", lambda: 3)

        await _run_parallel(_make_display(live=True), {"a": 0.01, "b": 0.02, "c": 0.03})

        out = capsys.readouterr().out
        assert len([l for l in _plain(out).splitlines() if l.startswith("· ")]) == 3 - 1
        assert "✓ c  c" in _plain(out)   # the third call is appended when it ends

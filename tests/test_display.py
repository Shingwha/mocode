"""Display — putting lines on a terminal, and the turn as it streams.

What a turn *looks like* is asserted in `test_lines.py`; anything that needs a
terminal is here.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import MagicMock

import pytest

from mocode.cli import lines
from mocode.cli.display import Display
from mocode.cli.hook import CLIDisplayHook
from mocode.cli.theme import Theme
from mocode.core import Agent, Event, Tool, ToolOutput, ToolRegistry, ToolResult
from mocode.core.provider import Response, ToolCall, Usage

from .providers import MockProvider, tool_call_response

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _make_display() -> Display:
    display = Display(input_=MagicMock(), theme=Theme())
    display.clear_screen = lambda: None  # never shell out from a test
    return display


def _plain(text: str) -> str:
    return ANSI.sub("", text)


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
        assert _make_display().format(lines.Line(text="x", style="nope")) == "x"


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


class TestFrontend:
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


class TestDisplayHook:
    """A whole turn, asserted through the hook a CLI actually installs."""

    async def _run(self, provider, *tools: Tool):
        display = _make_display()
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        agent = (
            Agent()
            .provider(provider)
            .prompt("t")
            .tools(registry)
            .hooks([CLIDisplayHook(display, registry)])
            .build()
        )
        async for _ in agent.stream("hi"):
            pass
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
        assert set(rendered[3]) == {"─"}  # the rule that closes the turn

    @pytest.mark.asyncio
    async def test_a_talkative_tool_reads_top_down(self, capsys):
        """Header, then its output, then the verdict — never output first."""

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
        block = [line for line in out.splitlines() if line[:1] in "→│✓✗"]

        assert block == ["→ make", "│ building", "│ linking", "✓"]

    @pytest.mark.asyncio
    async def test_stderr_is_marked_as_a_warning(self, capsys):
        async def noisy(args, ctx):
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text="careful\n", stream="stderr"))
            return "careful"

        await self._run(
            MockProvider([
                tool_call_response("warn"),
                Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            Tool("warn", "d", {}, noisy),
        )

        assert "│ careful" in _plain(capsys.readouterr().out)

    @pytest.mark.asyncio
    async def test_output_is_capped_and_the_omission_reported(self, capsys):
        async def flood(args, ctx):
            # One chunk, more lines than the budget — the cap counts lines.
            await ctx.emit(
                ToolOutput(call_id=ctx.tool_call_id, text="".join(f"L{i}\n" for i in range(30)))
            )
            return "done"

        await self._run(
            MockProvider([
                tool_call_response("flood"),
                Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            Tool("flood", "d", {}, flood),
        )

        out = _plain(capsys.readouterr().out)
        assert "│ L19" in out and "│ L20" not in out
        assert "… +10 more lines" in out

    @pytest.mark.asyncio
    async def test_parallel_calls_label_their_output(self, capsys):
        """Two blocks interleaving would be unattributable without a name."""

        async def chatty(args, ctx):
            # Both calls must still be in flight when each speaks, or this is
            # two sequential calls and no label is expected.
            await asyncio.sleep(0.01)
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text=f"from {args['tag']}\n"))
            await asyncio.sleep(0.01)
            return args["tag"]

        params = {"tag": {"type": "string", "description": "t"}}
        display = _make_display()
        registry = ToolRegistry()
        registry.register(Tool("a", "d", params, chatty, summary_key="tag"))
        registry.register(Tool("b", "d", params, chatty, summary_key="tag"))
        provider = MockProvider([
            Response(
                tool_calls=[
                    ToolCall(id="c1", name="a", arguments='{"tag": "one"}'),
                    ToolCall(id="c2", name="b", arguments='{"tag": "two"}'),
                ],
                usage=Usage(1, 1),
                finish_reason="tool_calls",
            ),
            Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
        ])
        agent = (
            Agent().provider(provider).prompt("t").tools(registry)
            .hooks([CLIDisplayHook(display, registry)]).build()
        )
        async for _ in agent.stream("hi"):
            pass

        out = _plain(capsys.readouterr().out)
        assert "│ a  from one" in out
        assert "│ b  from two" in out

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
        agent = (
            Agent()
            .provider(MockProvider([
                tool_call_response("risky"),
                Response(content="ok", usage=Usage(1, 1), finish_reason="stop"),
            ]))
            .prompt("t")
            .tools(registry)
            .hooks([Denier(), CLIDisplayHook(display, registry)])
            .build()
        )
        async for _ in agent.stream("hi"):
            pass

        out = _plain(capsys.readouterr().out)
        assert "✗ risky · denied (not allowed)" in out

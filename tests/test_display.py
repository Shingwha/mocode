"""Display — putting lines on a terminal, and a turn as it is drawn.

What a turn *looks like* is asserted in `test_lines.py`; anything that needs a
terminal is here. The renderer is driven the way the CLI drives it: a
conversation's event stream in, lines out.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

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
        """图标、文本、注记在同一行上各就各位；什么都不加时行首干净，
        认不出来的样式也原样上色失败，只有一个注记时不留空的分隔符。"""
        display = _make_display()
        got = display.format(
            lines.Line(
                text="read  a.py", icon="✓", icon_style="success",
                style="accent", note="lines=3",
            )
        )
        assert _plain(got) == "✓ read  a.py · lines=3"

        # 答案行前面什么都不加；认不出来的样式也原样上色失败——都不加前缀
        assert _plain(_make_display().format(lines.Line(text="hello"))) == "hello"
        assert _plain(_make_display().format(lines.Line(text="x", style="nope"))) == "x"

        # 注记单独在：分隔符不挂在空内容后面
        got = display.format(
            lines.Line(icon="✓", icon_style="success", note="lines=3")
        )
        assert _plain(got) == "✓ lines=3"


class TestClamping:
    """A block row has to be exactly one terminal row, or its offsets lie."""

    def test_what_fits_is_measured_and_overlong_ends_in_an_ellipsis(self):
        """宽度按显示列数算：ASCII 与宽字符（中日韩）一视同仁——放得下的
        原样留下，放不下的收尾成一个省略号。"""
        assert clamp_visible("read  a.py", 40) == "read  a.py"
        assert _plain(clamp_visible("x" * 100, 10)) == "x" * 9 + "…"
        assert _plain(clamp_visible("中文测试宽度", 8)) == "中文测…"

    def test_styling_survives_and_is_closed(self):
        got = clamp_visible("\033[2m" + "x" * 100 + "\033[0m", 10)
        assert got.startswith("\033[2m")
        assert got.endswith("\033[0m")
        assert _plain(got) == "x" * 9 + "…"

    def test_a_newline_cannot_split_the_row(self):
        """控制字符没有宽度可量却照样移动光标，而 wcswidth 对它返回 -1——
        不先压扁，块内行会占多行，后面所有行的偏移全是错的。"""
        # 预算内：原样压扁，一行、标记在
        got = clamp_visible("import asyncio\nmcode = asyncio.run\nprint(1)", 60)
        assert _plain(got) == "import asyncio\\nmcode = asyncio.run\\nprint(1)"

        # 超预算：一样先压扁再按列数收尾
        assert _plain(clamp_visible("a\nbb\nccc", 6)) == "a\\nbb…"


class TestStreaming:
    def test_answer_and_reasoning_stream_their_text_through(self, capsys):
        """流式输出的文本原样落笔：答案与推理都不加字形、不加缩进。"""
        display = _make_display()

        display.stream("Hel", kind="answer")
        display.stream("lo\nworld\n", kind="answer")
        display.end_stream()

        assert _plain(capsys.readouterr().out) == "Hello\nworld\n"

        display.stream("先看看\n再说\n", kind="reasoning")
        display.end_stream()

        assert _plain(capsys.readouterr().out) == "先看看\n再说\n"

    def test_the_open_block_is_closed_by_whatever_comes_next(self, capsys):
        """流的生命周期：换 kind、落一行，都先把开着的那块收掉——已经写上
        去的文本原样保留，不会被下一块吞掉。"""
        display = _make_display()

        display.stream("thinking", kind="reasoning")
        display.stream("answer", kind="answer")
        display.end_stream()

        assert _plain(capsys.readouterr().out) == "thinking\nanswer\n"

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

        # 没有 usage 的 endpoint 拿不到令牌行——那行不出现
        await self._run(
            MockProvider([
                Response(content="done", usage=Usage(0, 0), finish_reason="stop"),
            ]),
        )

        assert "tokens" not in _plain(capsys.readouterr().out)

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


async def _run_parallel(display: Display, order: list[str]):
    """One batch of parallel tool calls, finishing in a forced order.

    The order is the subject — the rows must not follow it — so it is
    established by events rather than by durations. Every call waits at a
    shared gate until all of them are running (so no verdict can be written
    before every row is claimed), and they are then released one at a time,
    *order* first (so the completion order is exact rather than whatever the
    timing happened to produce). A loop that ran the calls one at a time
    would never open the gate.
    """
    gate = asyncio.Event()
    finished = {name: asyncio.Event() for name in order}
    in_flight = 0

    async def slow(args, ctx):
        nonlocal in_flight
        in_flight += 1
        if in_flight == len(order):
            gate.set()  # every call is running; every row is claimed
        await gate.wait()
        for earlier in order[: order.index(args["tag"])]:
            await finished[earlier].wait()
        finished[args["tag"]].set()
        return args["tag"]

    schema = {
        "type": "object",
        "properties": {"tag": {"type": "string", "description": "t"}},
        "required": ["tag"],
    }
    registry = ToolRegistry()
    for name in order:
        registry.register(Tool(name, "d", schema, slow, summary_key="tag", with_context=True))
    provider = MockProvider([
        Response(
            tool_calls=[
                ToolCall(id=f"c{i}", name=name, arguments=f'{{"tag": "{name}"}}')
                for i, name in enumerate(order)
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

    async def draw_the_run():
        async for event in agent.stream("hi"):
            renderer.draw(event)

    # Bounded: a sequential dispatcher would hang on the gate above, and the
    # bound is what turns that hang into a plain test failure.
    await asyncio.wait_for(draw_the_run(), 5.0)


#: One in-place rewrite: move up n rows, clear the row, write the line, come
#: back down the same n — the backreference is half the assertion.
REWRITE = re.compile(r"\x1b\[(\d+)A\x1b\[K(.*?)\x1b\[\1B\r")


class TestLiveBlock:
    """A batch keeps one row per call — in call order, rewritten in place.

    On a terminal the row a call claims while it runs is the row its verdict
    lands on. These assert the escape sequence exactly, because the row offsets
    are the whole mechanism: an offset one row off corrupts the screen.
    """

    async def test_a_batch_keeps_one_row_per_call_in_call_order(self, capsys):
        # 'a' finishes first and 'c' last — the rows must not follow that.
        await _run_parallel(_make_display(live=True), ["a", "b", "c"])
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

    async def test_output_that_is_not_a_verdict_freezes_the_block(self, capsys):
        """A row is only rewritable while nothing else has been printed."""

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
        assert "careful" in _plain(out)           # what froze the block
        assert not REWRITE.search(out)            # so the verdict is appended
        assert "✓ noisy" in _plain(out)

    async def test_a_redirected_run_prints_no_placeholders(self, capsys):
        """A log cannot be rewritten, and would keep the dim rows forever."""
        await _run_parallel(_make_display(live=False), ["a", "b", "c"])

        out = _plain(capsys.readouterr().out)
        assert not any(line.startswith("· ") for line in out.splitlines())
        assert {line for line in out.splitlines() if line.startswith("✓")} == {
            "✓ a  a", "✓ b  b", "✓ c  c",
        }

    async def test_a_block_taller_than_the_screen_stops_claiming_rows(
        self, capsys, monkeypatch
    ):
        """Rows above the fold have scrolled away; their offsets mean nothing.

        屏幕高度是终端自己的事实，而 Display 只通过模块函数
        ``mocode.cli.display.terminal_height`` 读它——产品未提供构造注入点，
        这里从该 seam 换入固定高度（patch 接缝在此声明，不是私有属性）。
        """
        monkeypatch.setattr("mocode.cli.display.terminal_height", lambda: 3)

        await _run_parallel(_make_display(live=True), ["a", "b", "c"])

        out = capsys.readouterr().out
        assert len([l for l in _plain(out).splitlines() if l.startswith("· ")]) == 3 - 1
        assert "✓ c  c" in _plain(out)   # the third call is appended when it ends

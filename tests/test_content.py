"""Content rendering — fence tracking, the optional rich render, the drawer example.

What a streamed block looks like on screen is asserted byte for byte in
`test_display.py`; this file covers the pieces that need no terminal: the
fence state machine, the settle-time renderer's two availability paths, and
the message-drawers example plugin loading for real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mocode.cli import lines as L
from mocode.cli.markdown import FenceTracker, render_settled, style_code_line
from mocode.cli.plugin import (
    CLIContext,
    DrawerRegistry,
    HeaderRegistry,
    InputMiddleware,
    KeyRegistry,
    StatusRegistry,
    load_cli_plugins,
)
from mocode.cli.theme import Theme
from mocode.cli.transcript import Transcript
from mocode.core.events import PluginMessage, TextDelta, ToolCallStarted
from mocode.host.command import CommandRegistry
from mocode.host.plugin.context import BuildContext
from mocode.host.plugin.host import PluginHost, load_plugins

from .conftest import make_config, strip_ansi


class TestFenceTracker:
    def test_a_text_block_keeps_the_tracker_outside(self):
        tracker = FenceTracker()
        for line in ("just prose", "", "text with ``` inline", "   not a fence row"):
            tracker.feed(line)
        assert not tracker.in_fence()

    def test_a_fence_row_opens_and_the_next_line_is_inside(self):
        tracker = FenceTracker()
        tracker.feed("```python")  # the fence stands once its row completes
        assert tracker.in_fence()
        tracker.feed("x = 1")
        assert tracker.in_fence()

    def test_the_closing_row_closes(self):
        tracker = FenceTracker()
        tracker.feed("```")
        tracker.feed("x = 1")
        tracker.feed("```")
        assert not tracker.in_fence()

    def test_there_is_no_nesting(self):
        """A fence row inside a fence closes it — a streamed document is
        trusted over a recovered one."""
        tracker = FenceTracker()
        tracker.feed("```")
        tracker.feed("```python")
        assert not tracker.in_fence()
        tracker.feed("inner")
        assert not tracker.in_fence()

    def test_leading_whitespace_still_counts(self):
        tracker = FenceTracker()
        tracker.feed("  ```py")
        assert tracker.in_fence()

    def test_each_tracker_is_independent(self):
        first, second = FenceTracker(), FenceTracker()
        first.feed("```")
        assert first.in_fence() and not second.in_fence()


class TestStyleCodeLine:
    def test_a_code_line_is_de_emphasised(self):
        line = style_code_line("x = 1", Theme())
        assert line == L.Line(text="x = 1", style="dim")

    def test_a_theme_without_dim_falls_back_to_plain(self):
        theme = Theme(dim="")
        assert style_code_line("x = 1", theme).style == "answer"


class TestRenderSettled:
    def test_without_rich_it_declines_and_the_plain_lines_stay(self, monkeypatch):
        monkeypatch.setattr("mocode.cli.markdown.rich", None)
        assert render_settled("# Hi", "answer") is None

        transcript = Transcript(markdown=render_settled)
        transcript.apply(TextDelta(text="# Hi"))
        transcript.apply(ToolCallStarted(call_id="a", name="read"))  # seals the block
        assert transcript.blocks[0].lines == [L.Line(text="# Hi")]

    def test_with_rich_the_block_comes_back_as_ansi_rows(self, monkeypatch):
        pytest.importorskip("rich")
        monkeypatch.setattr("mocode.cli.markdown.terminal_width", lambda: 80)
        rows = render_settled("# Title\n\nsome **bold** text\n", "answer")
        assert rows is not None
        assert any("\x1b[" in row.text for row in rows)
        plain = [strip_ansi(row.text).strip() for row in rows]
        assert any("Title" in row for row in plain)
        assert any("bold" in row for row in plain)

    def test_a_sealed_block_uses_the_rendered_lines_when_served(self):
        rendered = [L.Line(text="rich row")]
        transcript = Transcript(markdown=lambda text, kind: rendered)
        transcript.apply(TextDelta(text="plain source"))
        transcript.apply(ToolCallStarted(call_id="a", name="read"))

        assert transcript.blocks[0].lines is rendered

    def test_a_rendered_none_falls_back_to_the_plain_vocabulary(self):
        transcript = Transcript(markdown=lambda text, kind: None)
        transcript.apply(TextDelta(text="plain"))
        transcript.apply(ToolCallStarted(call_id="a", name="read"))

        assert transcript.blocks[0].lines == [L.Line(text="plain")]


#: The shipped examples — loaded for real, the way a project would.
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "plugins"


class TestMessageDrawersExample:
    """examples/plugins/message-drawers — the official diff-drawer example,
    loaded and built through the same path a project's plugins take."""

    #: The plugin directory itself — the CLI loader reads `<dir>/mocode.cli`.
    PLUGIN = EXAMPLES / "message-drawers"

    @staticmethod
    def _host(tmp_path) -> BuildContext:
        """The example loads from the examples dir — the scanner's root,
        the way ``MoCode`` points a project's plugin directories at it."""
        ctx = BuildContext(home=tmp_path, cwd=tmp_path, config=make_config())
        loaded = load_plugins(plugin_dirs=[EXAMPLES], config=ctx.config)
        ctx.plugin_sources = list(loaded.sources)
        host = PluginHost(ctx, loaded.plugins, sources=loaded.tool_sources)
        host.build_all()
        return ctx

    def test_the_example_is_loaded_and_registers_its_tool(self, tmp_path):
        ctx = self._host(tmp_path)

        tool = ctx.tools.get("diff-demo")
        assert tool is not None and tool.wants_context
        assert tool.source == "plugin:message-drawers"

    def test_the_terminal_half_registers_the_diff_drawer(self, tmp_path):
        cli = load_cli_plugins([self.PLUGIN])
        assert [p.name for p in cli] == ["message-drawers.cli"]

        drawers = DrawerRegistry()
        ctx = CLIContext(
            commands=CommandRegistry(), drawers=drawers, ui=MagicMock(),
            keys=KeyRegistry(), input=InputMiddleware(),
            status=StatusRegistry(), header=HeaderRegistry(),
            theme=None, conversation=MagicMock(),
        )
        cli[0].build(ctx)

        event = PluginMessage(
            kind="diff-demo/patch",
            data={"diff": "@@ -1 +1 @@\n-old\n+new\n context"},
        )
        assert drawers.lines_for(event) == [
            L.Line(text="@@ -1 +1 @@", style="info"),
            L.Line(text="-old", style="error"),
            L.Line(text="+new", style="success"),
            L.Line(text=" context", style="dim"),
        ]

    @pytest.mark.asyncio
    async def test_a_run_emits_the_message_and_the_drawer_colours_it(self, tmp_path):
        """End to end: the tool runs, the message folds into a plugin block,
        and the registered drawer — not the summary fallback — shapes it."""
        from mocode.cli.transcript import Transcript
        from mocode.core import AgentLoop, HookRunner
        from mocode.core.provider import Response, Usage
        from mocode.testing import MockProvider, tool_call_response

        ctx = self._host(tmp_path)
        agent = AgentLoop(
            provider=MockProvider([
                tool_call_response("diff-demo"),
                Response(content="done", usage=Usage(1, 1), finish_reason="stop"),
            ]),
            system_prompt="t",
            tools=ctx.tools,
            hooks=HookRunner(),
        )
        events = [event async for event in agent.stream("hi")]

        drawers = DrawerRegistry()
        cli_ctx = CLIContext(
            commands=CommandRegistry(), drawers=drawers, ui=MagicMock(),
            keys=KeyRegistry(), input=InputMiddleware(),
            status=StatusRegistry(), header=HeaderRegistry(),
            theme=None, conversation=MagicMock(),
        )
        load_cli_plugins([self.PLUGIN])[0].build(cli_ctx)

        transcript = Transcript(ctx.tools, drawers=drawers)
        for event in events:
            transcript.apply(event)

        block = next(b for b in transcript.blocks if b.kind == "plugin")
        assert [line.style for line in block.lines] == [
            "error",  # --- before.txt
            "success",  # +++ after.txt
            "info",  # @@ -1,3 +1,4 @@
            "dim",  #  one
            "error",  # -two
            "success",  # +TWO
            "dim",  #  three
            "success",  # +four
        ]
        assert strip_ansi(block.lines[2].text).startswith("@@")

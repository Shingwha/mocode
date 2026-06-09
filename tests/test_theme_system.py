"""Tests for the new 4-layer theme system: Palette, Style, Styles, Theme."""

from __future__ import annotations

import pytest

from mocode.app.cli.palette import C, ColorPalette, DEFAULT_PALETTE
from mocode.app.cli.style import Style
from mocode.app.cli.styles import DisplayStyles, WorkflowStyles, SpinnerStyles
from mocode.app.cli.theme import Theme


# ── ColorPalette ──────────────────────────────────────────────


class TestColorPalette:
    """ColorPalette: semantic color mapping."""

    def test_default_values(self):
        p = DEFAULT_PALETTE
        assert p.success == C.GREEN
        assert p.error == C.RED
        assert p.warning == C.YELLOW
        assert p.info == C.SOFT_CYAN
        assert p.bold == C.BOLD
        assert p.dim == C.DIM
        assert p.muted == C.GRAY
        assert p.accent == C.CYAN
        assert p.highlight == C.MAGENTA
        assert p.bg_input == C.BG_DARK
        assert p.reset == C.RST

    def test_resolve_semantic_name(self):
        p = DEFAULT_PALETTE
        assert p.resolve("success") == C.GREEN
        assert p.resolve("error") == C.RED
        assert p.resolve("dim") == C.DIM

    def test_resolve_passthrough_escape(self):
        p = DEFAULT_PALETTE
        assert p.resolve("\033[92m") == "\033[92m"
        assert p.resolve("\033[1m") == "\033[1m"

    def test_resolve_empty_string(self):
        p = DEFAULT_PALETTE
        assert p.resolve("") == ""

    def test_resolve_compound(self):
        p = DEFAULT_PALETTE
        assert p.resolve("dim highlight") == C.DIM + C.MAGENTA

    def test_resolve_compound_triple(self):
        p = DEFAULT_PALETTE
        assert p.resolve("bold success") == C.BOLD + C.GREEN

    def test_resolve_unknown_raises(self):
        p = DEFAULT_PALETTE
        with pytest.raises(ValueError, match="Unknown color"):
            p.resolve("nonexistent")

    def test_s_method(self):
        p = DEFAULT_PALETTE
        result = p.s("hello", "success")
        assert result == f"{C.GREEN}hello{C.RST}"

    def test_s_multiple_colors(self):
        p = DEFAULT_PALETTE
        result = p.s("hello", "success", "bold")
        assert result == f"{C.GREEN}{C.BOLD}hello{C.RST}"

    def test_s_no_colors(self):
        p = DEFAULT_PALETTE
        result = p.s("hello")
        assert result == "hello"

    def test_frozen(self):
        p = DEFAULT_PALETTE
        with pytest.raises(AttributeError):
            p.success = C.RED

    def test_custom_palette(self):
        p = ColorPalette(success="\033[32m")
        assert p.success == "\033[32m"
        assert p.error == C.RED  # other defaults unchanged


# ── Style ─────────────────────────────────────────────────────


class TestStyle:
    """Style: atomic style descriptor with palette-aware rendering."""

    def test_render_basic_text(self):
        s = Style(fg="success")
        result = s.render("hello", DEFAULT_PALETTE)
        assert result == f"{C.GREEN}hello{C.RST}"

    def test_render_icon(self):
        s = Style(icon="✓", fg="success")
        result = s.render("tool", DEFAULT_PALETTE)
        assert "✓" in result
        assert C.GREEN in result

    def test_render_icon_fg_overrides(self):
        s = Style(icon="✗", icon_fg="error", fg="accent")
        result = s.render("fail", DEFAULT_PALETTE)
        # icon uses error color, text uses accent
        assert C.RED in result   # icon_fg
        assert C.CYAN in result  # fg

    def test_render_suffix(self):
        s = Style(fg="bold")
        result = s.render("tool", DEFAULT_PALETTE, suffix="(args)")
        assert "(args)" in result
        assert C.DIM in result  # suffix is dimmed

    def test_render_elapsed(self):
        s = Style(fg="bold")
        result = s.render("tool", DEFAULT_PALETTE, elapsed=1.5)
        assert "1.5s" in result
        assert C.DIM in result

    def test_render_elapsed_below_threshold(self):
        s = Style(fg="bold")
        result = s.render("tool", DEFAULT_PALETTE, elapsed=0.05)
        assert "0.1s" not in result  # below 0.1 threshold

    def test_render_error(self):
        s = Style(icon_fg="error", fg="accent")
        result = s.render("tool", DEFAULT_PALETTE, error="something broke")
        assert "something broke" in result

    def test_render_no_style(self):
        s = Style()
        result = s.render("plain text", DEFAULT_PALETTE)
        assert result == "plain text"

    def test_render_bg(self):
        s = Style(fg="bold", bg="bg_input")
        result = s.render("input", DEFAULT_PALETTE)
        assert C.BG_DARK in result
        assert C.BOLD in result

    def test_frozen(self):
        s = Style(icon="✓")
        with pytest.raises(AttributeError):
            s.icon = "✗"


# ── Component Styles ──────────────────────────────────────────


class TestDisplayStyles:
    """DisplayStyles: default values for Display component."""

    def test_tool_done(self):
        ds = DisplayStyles()
        assert ds.tool_done.icon == "✓"
        assert ds.tool_done.icon_fg == "success"
        assert ds.tool_done.fg == "accent"

    def test_tool_fail(self):
        ds = DisplayStyles()
        assert ds.tool_fail.icon == "✗"
        assert ds.tool_fail.icon_fg == "error"

    def test_user_input(self):
        ds = DisplayStyles()
        assert ds.user_input.icon == "❯"
        assert ds.user_input.bg == "bg_input"

    def test_reasoning(self):
        ds = DisplayStyles()
        assert ds.reasoning.icon == "┊"
        assert ds.reasoning.fg == "dim"

    def test_frozen(self):
        ds = DisplayStyles()
        with pytest.raises(AttributeError):
            ds.tool_done = Style()


class TestWorkflowStyles:
    """WorkflowStyles: default values for WorkflowRenderer component."""

    def test_wave(self):
        ws = WorkflowStyles()
        assert ws.wave.icon == "◇"
        assert ws.wave.fg == "warning"

    def test_node_done_ok(self):
        ws = WorkflowStyles()
        assert ws.node_done_ok.icon == "■"
        assert ws.node_done_ok.fg == "success"

    def test_node_done_fail(self):
        ws = WorkflowStyles()
        assert ws.node_done_fail.icon == "■"
        assert ws.node_done_fail.fg == "error"

    def test_router_match(self):
        ws = WorkflowStyles()
        assert ws.router_match.icon == "▸"
        assert ws.router_match.fg == "info"

    def test_loop(self):
        ws = WorkflowStyles()
        assert ws.loop.icon == "↻"
        assert ws.loop.fg == "warning"

    def test_frozen(self):
        ws = WorkflowStyles()
        with pytest.raises(AttributeError):
            ws.wave = Style()


class TestSpinnerStyles:
    """SpinnerStyles: default values for Spinner component."""

    def test_defaults(self):
        ss = SpinnerStyles()
        assert ss.frame == "dim"
        assert ss.elapsed == "info"

    def test_frozen(self):
        ss = SpinnerStyles()
        with pytest.raises(AttributeError):
            ss.frame = "bold"


# ── Theme ─────────────────────────────────────────────────────


class TestTheme:
    """Theme: composition wrapper."""

    def test_default_composition(self):
        t = Theme()
        assert isinstance(t.display, DisplayStyles)
        assert isinstance(t.workflow, WorkflowStyles)
        assert isinstance(t.spinner, SpinnerStyles)
        assert isinstance(t.palette, ColorPalette)

    def test_for_display(self):
        t = Theme()
        ds, p = t.for_display()
        assert isinstance(ds, DisplayStyles)
        assert isinstance(p, ColorPalette)

    def test_for_workflow(self):
        t = Theme()
        ws, p = t.for_workflow()
        assert isinstance(ws, WorkflowStyles)
        assert isinstance(p, ColorPalette)

    def test_for_spinner(self):
        t = Theme()
        ss, p = t.for_spinner()
        assert isinstance(ss, SpinnerStyles)
        assert isinstance(p, ColorPalette)

    def test_custom_palette(self):
        custom = ColorPalette(success="\033[32m")
        t = Theme(palette=custom)
        ds, p = t.for_display()
        assert p.success == "\033[32m"

    def test_custom_display_styles(self):
        custom_ds = DisplayStyles(tool_done=Style(icon="✔", fg="success"))
        t = Theme(display=custom_ds)
        ds, _ = t.for_display()
        assert ds.tool_done.icon == "✔"

    def test_custom_workflow_styles(self):
        custom_ws = WorkflowStyles(node_start=Style(icon="▶", fg="bold"))
        t = Theme(workflow=custom_ws)
        ws, _ = t.for_workflow()
        assert ws.node_start.icon == "▶"

    def test_components_independent(self):
        """Changing workflow styles should not affect display styles."""
        t = Theme(
            workflow=WorkflowStyles(node_start=Style(icon="▶", fg="bold"))
        )
        ds, _ = t.for_display()
        ws, _ = t.for_workflow()
        # Display is still default
        assert ds.tool_done.icon == "✓"
        # Workflow is customized
        assert ws.node_start.icon == "▶"

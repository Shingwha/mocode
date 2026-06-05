"""CLI visual configuration — ANSI styles, Style, Theme."""

from __future__ import annotations

from dataclasses import dataclass

# ── ANSI constants ───────────────────────────────────────

RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GRAY = "\033[90m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
SOFT_CYAN = "\033[36m"
BG_USER = "\033[100m"


# ── Style ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Style:
    """Visual style for a single display line.

    Composable and declarative — define once in Theme, use everywhere.

    Example::

        tool_done = Style(icon="✓", icon_color=GREEN, text_color=CYAN)
        info      = Style(text_color=SOFT_CYAN)
    """

    icon: str = ""
    icon_color: str = ""        # defaults to text_color when empty
    text_color: str = ""
    bg: str = ""                # optional background (e.g. BG_USER)


# ── Theme ───────────────────────────────────────────────


@dataclass
class Theme:
    """Visual style — swap to change the CLI look.

    Each field is a ``Style`` instance. Override to reskin the entire CLI::

        Theme(style_tool_done=Style(icon="✔", icon_color=GREEN, text_color=CYAN))
    """

    # Tool lifecycle
    style_tool_done: Style = Style(icon="✓", icon_color=GREEN, text_color=CYAN)
    style_tool_fail: Style = Style(icon="✗", icon_color=RED, text_color=CYAN)

    # User input
    style_user: Style = Style(icon="❯", icon_color=BOLD, text_color=BOLD, bg=BG_USER)

    # Model response
    style_reasoning: Style = Style(icon="┊", text_color=DIM)
    style_text: Style = Style(icon="│", text_color=DIM + MAGENTA)
    style_response: Style = Style()  # raw text, no styling

    # Status
    style_usage: Style = Style(icon="✦", text_color=DIM)
    style_compact: Style = Style(icon="─", text_color=YELLOW)
    style_info: Style = Style(text_color=SOFT_CYAN)
    style_warn: Style = Style(text_color=YELLOW)
    style_error: Style = Style(text_color=RED)


# ── Helpers ─────────────────────────────────────────────


def _s(text: str, *codes: str) -> str:
    """Apply ANSI codes to text with reset."""
    return f"{''.join(codes)}{text}{RST}"


def questionary_style(theme: Theme | None = None):
    """Build a questionary Style matching the CLI theme."""
    from questionary import Style as _QStyle

    return _QStyle(
        [
            ("qmark", "fg:ansicyan bold"),
            ("question", "bold"),
            ("answer", "fg:ansigreen bold"),
            ("pointer", "fg:ansicyan bold"),
            ("highlighted", "fg:ansicyan bold"),
            ("selected", "fg:ansigreen"),
            ("separator", "fg:ansibrightblack"),
            ("instruction", "fg:ansibrightblack"),
            ("text", ""),
            ("disabled", "fg:ansibrightblack italic"),
        ]
    )

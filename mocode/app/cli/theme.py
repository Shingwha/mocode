"""CLI visual configuration — ANSI styles, Spinner presets, Style, Theme."""

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


# ── Spinner ─────────────────────────────────────────────


def _ping_pong(frames: list[str]) -> list[str]:
    """Forward then reverse, skipping duplicate endpoints."""
    return frames + frames[-2:0:-1]


@dataclass(frozen=True, slots=True)
class Spinner:
    """A named spinner style."""

    frames: tuple[str, ...]
    speed: float = 0.08
    show_text: bool = True

    @staticmethod
    def from_list(
        frames: list[str], speed: float = 0.08, show_text: bool = True
    ) -> "Spinner":
        return Spinner(frames=tuple(frames), speed=speed, show_text=show_text)


# Built-in presets
_PRESETS: dict[str, Spinner] = {
    "braille": Spinner.from_list(list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"), 0.12),
    "sweep": Spinner.from_list(
        _ping_pong([f"{'░' * i}█{'░' * (9 - i)}" for i in range(10)]), 0.10
    ),
    "chase": Spinner.from_list(
        [" ".join("●" if j == i else "○" for j in range(5)) for i in range(5)], 0.18
    ),
    # --- Fun additions ---
    "triangle": Spinner(frames=("△", "▷", "▽", "◁"), speed=0.18),
    "wave": Spinner.from_list(
        ["".join("▁▂▃▄▅▆▇█▇▆▅▄▃▂"[(i + j) % 14] for j in range(10)) for i in range(14)],
        0.10,
    ),
    "fill": Spinner(frames=("░", "▒", "▓", "█", "▓", "▒"), speed=0.15),
    "music": Spinner(frames=("♩", "♪", "♫", "♬"), speed=0.30),
    "chess": Spinner(frames=("♔", "♕", "♖", "♗", "♘", "♙"), speed=0.25),
    "math": Spinner(frames=("∑", "∏", "∫", "∂", "∇", "√"), speed=0.28),
    "bounce": Spinner.from_list(
        _ping_pong(["●····", "·●···", "··●··", "···●·", "····●"]), 0.15
    ),
    "signal": Spinner(frames=("○○○○", "●○○○", "●●○○", "●●●○", "●●●●"), speed=0.25),
    "equalizer": Spinner(
        frames=(
            "▃▅▇",
            "▅▇▅",
            "▇▅▃",
            "▅▃▁",
            "▃▁▃",
            "▁▃▅",
        ),
        speed=0.12,
    ),
    "ripple": Spinner(frames=("···", "·∘·", "∘○∘", "○◌○", "◌·◌"), speed=0.2),
    "rain": Spinner(frames=("│···", "·│··", "··│·", "···│"), speed=0.15),
    "scroll": Spinner(
        frames=(
            "░▒▓█▓▒░",
            "▒▓█▓▒░░",
            "▓█▓▒░░░",
            "█▓▒░░░░",
            "▓▒░░░░░",
            "▒░░░░░░",
            "░░░░░░░",
            "░░░░░░▒",
            "░░░░░▒▓",
            "░░░░▒▓█",
            "░░░▒▓█▓",
            "░░▒▓█▓▒",
            "░▒▓█▓▒░",
        ),
        speed=0.06,
    ),
    # --- Single-emoji spinners (one emoji per frame, width-stable) ---
    "globe": Spinner(frames=("🌍", "🌎", "🌏"), speed=0.45),
    "clock": Spinner(
        frames=("🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"),
        speed=0.15,
    ),
    # --- Variable-width spinners ---
    "grow": Spinner.from_list(
        _ping_pong(["·", "··", "···", "····", "·····", "······", "·······"]), 0.18
    ),
    "typewriter": Spinner.from_list(
        _ping_pong(["▌", "思▌", "思考▌", "思考中▌", "思考中…▌", "思考中… ▌"]), 0.20
    ),
    "train": Spinner.from_list(
        _ping_pong(["🚂", "🚂🚃", "🚂🚃🚃", "🚂🚃🚃🚃"]), 0.25
    ),
    "snake": Spinner.from_list(
        _ping_pong(["●", "○●", "○○●", "○○○●", "○○○○●", "○○○○○●"]), 0.18
    ),
    "progress": Spinner.from_list(
        _ping_pong(["[    ]", "[-   ]", "[--  ]", "[--- ]", "[----]"]), 0.20
    ),
}


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

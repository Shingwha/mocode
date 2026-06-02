"""CLI visual configuration — ANSI styles, Spinner presets, Theme."""

from dataclasses import dataclass, field
from wcwidth import wcswidth

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
BG_USER = "\033[100m"

COMMANDS = ["/export", "/resume", "/sessions", "/clear", "/quit", "/exit"]


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

    def __post_init__(self):
        max_w = max(max(wcswidth(f), 0) for f in self.frames)
        padded = tuple(f + " " * (max_w - max(wcswidth(f), 0)) for f in self.frames)
        object.__setattr__(self, "frames", padded)

    @staticmethod
    def from_list(frames: list[str], speed: float = 0.08, show_text: bool = True) -> "Spinner":
        return Spinner(frames=tuple(frames), speed=speed, show_text=show_text)


# Built-in presets
_PRESETS: dict[str, Spinner] = {
    "braille": Spinner.from_list(list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"), 0.12),
    "sweep":   Spinner.from_list(_ping_pong([f"{'░'*i}█{'░'*(9-i)}" for i in range(10)]), 0.10),
    "chase":   Spinner.from_list([' '.join('●' if j == i else '○' for j in range(5)) for i in range(5)], 0.18),

    # --- Fun additions ---

    "triangle": Spinner(frames=('△', '▷', '▽', '◁'), speed=0.18),
    "wave":     Spinner.from_list([
        ''.join('▁▂▃▄▅▆▇█▇▆▅▄▃▂'[(i + j) % 14] for j in range(10))
        for i in range(14)
    ], 0.10),
    "fill":     Spinner(frames=('░', '▒', '▓', '█', '▓', '▒'), speed=0.15),
    "music":    Spinner(frames=('♩', '♪', '♫', '♬'), speed=0.30),
    "chess":    Spinner(frames=('♔', '♕', '♖', '♗', '♘', '♙'), speed=0.25),
    "math":     Spinner(frames=('∑', '∏', '∫', '∂', '∇', '√'), speed=0.28),
    "bounce":   Spinner.from_list(_ping_pong(['●····', '·●···', '··●··', '···●·', '····●']), 0.15),
    "signal":   Spinner(frames=('○○○○', '●○○○', '●●○○', '●●●○', '●●●●'), speed=0.25),
    "equalizer": Spinner(frames=(
        '▃▅▇', '▅▇▅', '▇▅▃', '▅▃▁', '▃▁▃', '▁▃▅',
    ), speed=0.12),
    "ripple":   Spinner(frames=('···', '·∘·', '∘○∘', '○◌○', '◌·◌'), speed=0.2),
    "rain":     Spinner(frames=('│···', '·│··', '··│·', '···│'), speed=0.15),
    "scroll":   Spinner(frames=(
        '░▒▓█▓▒░', '▒▓█▓▒░░', '▓█▓▒░░░', '█▓▒░░░░',
        '▓▒░░░░░', '▒░░░░░░', '░░░░░░░', '░░░░░░▒',
        '░░░░░▒▓', '░░░░▒▓█', '░░░▒▓█▓', '░░▒▓█▓▒', '░▒▓█▓▒░',
    ), speed=0.06),

    # --- Single-emoji spinners (one emoji per frame, width-stable) ---

    "globe":     Spinner(frames=('🌍', '🌎', '🌏'), speed=0.45),
    "clock":     Spinner(frames=('🕐', '🕑', '🕒', '🕓', '🕔', '🕕', '🕖', '🕗', '🕘', '🕙', '🕚', '🕛'), speed=0.15),
}


# ── Theme ───────────────────────────────────────────────


@dataclass
class Theme:
    """Visual style — swap to change the CLI look."""
    icon_tool: str = "→"
    icon_error: str = "×"
    icon_usage: str = "✦"
    icon_reasoning: str = "┊"
    icon_text: str = "│"
    icon_compact: str = "─"
    icon_input: str = "❯"
    color_tool: str = CYAN
    color_error: str = RED
    color_reasoning: str = DIM
    color_text: list[str] = field(default_factory=lambda: [DIM, MAGENTA])
    color_usage: str = DIM
    color_compact: str = YELLOW
    color_info: str = GREEN
    color_warn: str = YELLOW
    color_user_fg: list[str] = field(default_factory=lambda: [BOLD])


# ── Helpers ─────────────────────────────────────────────


def _s(text, *codes):
    return f"{''.join(codes)}{text}{RST}"

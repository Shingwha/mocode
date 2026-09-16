"""CLI theme — ANSI tokens, semantic palette, styles, and the Theme bundle."""

from __future__ import annotations

from dataclasses import dataclass, field


class C:
    """ANSI escape tokens — the only place raw codes are defined."""

    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GRAY = "\033[90m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    SOFT_CYAN = "\033[36m"
    BG_DARK = "\033[100m"


@dataclass(frozen=True)
class ColorPalette:
    """Semantic colour names → ANSI codes. Re-skin by replacing this object."""

    # status
    success: str = C.GREEN
    error: str = C.RED
    warning: str = C.YELLOW
    info: str = C.SOFT_CYAN

    # hierarchy
    bold: str = C.BOLD
    dim: str = C.DIM
    muted: str = C.GRAY
    accent: str = C.CYAN
    highlight: str = C.MAGENTA

    # surfaces
    bg_input: str = C.BG_DARK
    reset: str = C.RST

    def resolve(self, name: str) -> str:
        """Semantic name → ANSI code. Already-escaped values pass through.

        Space-separated names combine, e.g. ``"dim highlight"``.
        """
        if not name:
            return ""
        if name.startswith("\033["):
            return name
        codes = []
        for part in name.split():
            value = getattr(self, part, None)
            if value is None:
                raise ValueError(f"Unknown color: {part!r}")
            codes.append(value)
        return "".join(codes)

    def s(self, text: str, *colors: str) -> str:
        """Colour *text* with the given semantic colour names."""
        codes = "".join(self.resolve(c) for c in colors)
        return f"{codes}{text}{self.reset}" if codes else text


DEFAULT_PALETTE = ColorPalette()


@dataclass(frozen=True, slots=True)
class Style:
    """Atomic style descriptor — appearance only, no rendering policy.

    ``fg`` / ``icon_fg`` / ``bg`` are semantic colour names resolved by a palette.
    """

    icon: str = ""
    fg: str = ""
    icon_fg: str = ""  # defaults to fg
    bg: str = ""

    def render(
        self,
        text: str,
        palette: ColorPalette,
        *,
        suffix: str = "",
        elapsed: float = -1,
        error: str = "",
    ) -> str:
        """Render one line as an ANSI string."""
        parts: list[str] = []
        p = palette

        if self.icon:
            parts.append(f"{p.resolve(self.icon_fg or self.fg)}{self.icon}{p.reset}")

        fg = p.resolve(self.fg)
        bg = p.resolve(self.bg)
        if bg and fg:
            parts.append(f"{bg}{fg}{text}{p.reset}")
        elif fg:
            parts.append(f"{fg}{text}{p.reset}")
        else:
            parts.append(text)

        if suffix:
            parts.append(f"{p.dim}{suffix}{p.reset}")
        if error:
            parts.append(f"{p.resolve(self.icon_fg or self.fg)}{error}{p.reset}")
        if elapsed >= 0.1:
            parts.append(f"{p.dim}{elapsed:.1f}s{p.reset}")

        return " ".join(parts)


@dataclass(frozen=True)
class DisplayStyles:
    """Styles used by Display."""

    tool_done: Style = Style(icon="✓", icon_fg="success", fg="accent")
    tool_fail: Style = Style(icon="✗", icon_fg="error", fg="accent")
    user_input: Style = Style(icon="❯", fg="bold", bg="bg_input")
    reasoning: Style = Style(icon="┊", fg="dim")
    text: Style = Style(icon="│", fg="dim highlight")
    response: Style = Style()
    usage: Style = Style(icon="✦", fg="dim")
    info: Style = Style(fg="info")
    warn: Style = Style(fg="warning")
    error: Style = Style(fg="error")


@dataclass(frozen=True)
class SpinnerStyles:
    """Styles used by the spinner."""

    frame: str = "dim"
    elapsed: str = "info"


@dataclass
class Theme:
    """Bundle of palette plus per-component styles.

    Components receive only the subset they need, never the whole Theme::

        Theme(palette=ColorPalette(success="\\033[92m"))
        Theme(display=DisplayStyles(tool_done=Style(icon="✔", fg="success")))
    """

    palette: ColorPalette = field(default_factory=lambda: DEFAULT_PALETTE)
    display: DisplayStyles = field(default_factory=DisplayStyles)
    spinner: SpinnerStyles = field(default_factory=SpinnerStyles)

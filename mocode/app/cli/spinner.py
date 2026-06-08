"""Spinner — async animated spinner state machine with composable segments."""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum, IntEnum

from ..utils import ellipsize_middle, ellipsize_tail, terminal_width, visible_width
from .theme import DIM, RST, SOFT_CYAN


# ── Spinner style ────────────────────────────────────────────


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
    # --- Track animation (fixed-width) ---
    "runner": Spinner.from_list(
        _ping_pong([f"{'─' * i}🏃{'─' * (14 - i)}" for i in range(15)]), 0.10
    ),
}


# ── Enums and data classes ─────────────────────────────────────


class Truncate(Enum):
    """Segment truncation strategy."""

    TAIL = "tail"      # 尾部截断，保护前缀: "running 2 to..."
    MIDDLE = "middle"  # 中间截断，保留首尾: "read(src/.../file.py)"
    NONE = "none"      # 永不截断，永不移除（spinner 帧等固定内容）


class Priority(IntEnum):
    """Segment priority for truncation ordering (lower = truncate first).

    Usage:
        HIGH (20)   — Critical info, never truncate first (node IDs, tool names)
        NORMAL (10) — Important labels, truncate last (status text, phase names)
        LOW (5)     — Supplementary info, truncate first (detail, file paths)
    """

    LOW = 5       # 先被截（detail、补充信息）
    NORMAL = 10   # 中等保护（tag、标识）
    HIGH = 20     # 最后才截（关键状态）


@dataclass
class Segment:
    """A composable display segment with independent truncation control."""

    id: str
    text: str
    priority: int = Priority.NORMAL
    truncate: Truncate = Truncate.TAIL


# ── Formatting helpers ──────────────────────────────────────────


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as '1h 3m', '5m 2s', or '47s'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


# ── Truncation algorithm ───────────────────────────────────────


def _truncate_segs(segs: list[Segment], avail: int) -> list[Segment]:
    """Unified truncation: truncate TAIL/MIDDLE, then remove if still over min width.

    Phase 1: Truncate TAIL/MIDDLE segments (low priority first, high priority last)
    Phase 2: Segments still exceeding min width after truncation → remove
    Phase 3: NONE segments are never touched
    """
    result = [Segment(s.id, s.text, s.priority, s.truncate) for s in segs]

    def _total_width() -> int:
        return sum(visible_width(s.text) for s in result)

    # Phase 1: truncate TAIL/MIDDLE segments, low priority first
    truncatable = sorted(
        [i for i, s in enumerate(result) if s.truncate in (Truncate.TAIL, Truncate.MIDDLE)],
        key=lambda i: (result[i].priority, i),
    )
    for tidx in truncatable:
        if _total_width() <= avail:
            break
        seg = result[tidx]
        over = _total_width() - avail
        cur = visible_width(seg.text)
        min_w = 8 if seg.truncate == Truncate.TAIL else 12
        new_w = max(min_w, cur - over)
        if new_w >= cur:
            continue
        if seg.truncate == Truncate.TAIL:
            result[tidx] = Segment(seg.id, ellipsize_tail(seg.text, new_w),
                                   seg.priority, seg.truncate)
        else:
            result[tidx] = Segment(seg.id, ellipsize_middle(seg.text, new_w),
                                   seg.priority, seg.truncate)

    # Phase 2: remove TAIL/MIDDLE segments still over their min width
    removable = sorted(
        [i for i, s in enumerate(result) if s.truncate in (Truncate.TAIL, Truncate.MIDDLE)],
        key=lambda i: (result[i].priority, -i),
    )
    for idx in removable:
        if _total_width() <= avail:
            break
        seg = result[idx]
        min_w = 8 if seg.truncate == Truncate.TAIL else 12
        if visible_width(seg.text) <= min_w:
            result[idx] = Segment(seg.id, "", seg.priority, seg.truncate)

    # filter empty
    result = [s for s in result if s.text]

    return result


# ── SpinnerRunner ───────────────────────────────────────────────


class SpinnerRunner:
    """Owns the spinner animation lifecycle with composable segments."""

    def __init__(self, separator: str = " · ") -> None:
        self._active = False
        self._start: float = 0.0
        self._segments: dict[str, Segment] = {}  # 保持插入顺序
        self._separator = separator

    @property
    def active(self) -> bool:
        return self._active

    # ── Segment management ──────────────────────────────────────

    def set(self, id: str, text: str = "",
            priority: int | Priority = Priority.NORMAL,
            truncate: Truncate | str = Truncate.TAIL) -> None:
        """添加或更新一个 segment。text="" 则移除。"""
        if isinstance(truncate, str):
            truncate = Truncate(truncate)
        if not text:
            self._segments.pop(id, None)
            return
        self._segments[id] = Segment(
            id=id, text=text.replace("\n", " "),
            priority=int(priority), truncate=truncate,
        )

    def remove(self, id: str) -> None:
        """移除一个 segment。"""
        self._segments.pop(id, None)

    def clear(self) -> None:
        """移除所有 segment。"""
        self._segments.clear()

    # ── Rendering ───────────────────────────────────────────────

    def _render_suffix(self, max_width: int, spinner_show_text: bool) -> str:
        """渲染 suffix 部分（segment + elapsed）。"""
        if not spinner_show_text:
            return ""

        elapsed = _format_elapsed(time.monotonic() - self._start)
        elapsed_str = f"{SOFT_CYAN}{elapsed}{RST}"
        elapsed_vis = len(elapsed)

        segs = list(self._segments.values())
        if not segs:
            return f" {elapsed_str}"

        sep_vis = visible_width(self._separator)
        fixed = sep_vis * (len(segs) - 1) + elapsed_vis + 1
        avail = max_width - fixed

        # 深拷贝 segments 用于截断（不修改原始数据）
        render_segs = [Segment(s.id, s.text, s.priority, s.truncate) for s in segs]

        total = sum(visible_width(s.text) for s in render_segs)
        if total > avail:
            render_segs = _truncate_segs(render_segs, avail)

        joined = self._separator.join(s.text for s in render_segs)
        return f" {joined} {elapsed_str}"

    # ── spin() lifecycle ────────────────────────────────────────

    @staticmethod
    def resolve(style: str | Spinner | None = None) -> Spinner:
        """Resolve a spinner style name or object to a Spinner instance."""
        if style is None:
            return random.choice(list(_PRESETS.values()))
        if isinstance(style, Spinner):
            return style
        if style in _PRESETS:
            return _PRESETS[style]
        raise ValueError(
            f"Unknown spinner style: {style!r}. Available: {list(_PRESETS)}"
        )

    @asynccontextmanager
    async def spin(self, style: str | Spinner | None = None):
        """Shows a spinner while waiting."""
        spinner = self.resolve(style)
        self._active = True
        self._start = time.monotonic()
        self._segments.clear()
        stop = asyncio.Event()
        idx = 0

        async def _spin():
            nonlocal idx
            frame_interval = spinner.speed
            text_interval = 0.05
            last_frame = time.monotonic()
            frame = spinner.frames[0]

            while not stop.is_set():
                now = time.monotonic()
                if now - last_frame >= frame_interval:
                    idx += 1
                    frame = spinner.frames[idx % len(spinner.frames)]
                    last_frame = now

                term_w = terminal_width()
                max_suffix = max(10, int(term_w * 0.9) - 15)
                suffix = self._render_suffix(max_suffix, spinner.show_text)
                print(f"\r{DIM}{frame}{suffix}{RST}\033[K", end="", flush=True)
                await asyncio.sleep(text_interval)

        task = asyncio.ensure_future(_spin())
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._clear_line()
            self._active = False

    def _clear_line(self):
        print("\r\033[K", end="", flush=True)

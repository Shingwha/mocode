"""Spinner — async animated spinner state machine with composable segments."""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum, IntEnum

from .textutils import ellipsize_middle, ellipsize_tail, terminal_width, visible_width
from .theme import DIM, RST, SOFT_CYAN, Spinner, _PRESETS


# ── Enums and data classes ─────────────────────────────────────


class Truncate(Enum):
    """Segment truncation strategy."""

    TAIL = "tail"      # 尾部截断，保护前缀: "analy..."
    MIDDLE = "middle"  # 中间截断，保留首尾: "ana...ze"
    NONE = "none"      # 不截断


class Priority(IntEnum):
    """Segment priority for truncation ordering (lower = truncate first)."""

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
    """按优先级截断 segments 直到总宽度 <= avail。返回新列表。"""
    # 排序：priority ASC, 插入序 DESC（靠后的先截）
    order = sorted(range(len(segs)),
                   key=lambda i: (segs[i].priority, -i))

    for idx in order:
        total = sum(visible_width(s.text) for s in segs)
        if total <= avail:
            break
        seg = segs[idx]
        if seg.truncate == Truncate.NONE:
            continue
        over = total - avail
        cur = visible_width(seg.text)
        min_w = 4 if seg.truncate == Truncate.TAIL else 7
        new_w = max(min_w, cur - over)
        if new_w >= cur:
            continue
        if seg.truncate == Truncate.TAIL:
            segs[idx] = Segment(seg.id, ellipsize_tail(seg.text, new_w),
                                seg.priority, seg.truncate)
        else:
            segs[idx] = Segment(seg.id, ellipsize_middle(seg.text, new_w),
                                seg.priority, seg.truncate)

    return segs


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

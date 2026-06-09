"""Component-specific style groups — each component owns its own styles."""

from __future__ import annotations

from dataclasses import dataclass

from .style import Style


@dataclass(frozen=True)
class DisplayStyles:
    """Display 组件的样式。"""

    tool_done: Style = Style(icon="✓", icon_fg="success", fg="accent")
    tool_fail: Style = Style(icon="✗", icon_fg="error", fg="accent")
    user_input: Style = Style(icon="❯", fg="bold", bg="bg_input")
    reasoning: Style = Style(icon="┊", fg="dim")
    text: Style = Style(icon="│", fg="dim highlight")
    response: Style = Style()
    usage: Style = Style(icon="✦", fg="dim")
    compact: Style = Style(icon="─", fg="warning")
    info: Style = Style(fg="info")
    warn: Style = Style(fg="warning")
    error: Style = Style(fg="error")


@dataclass(frozen=True)
class WorkflowStyles:
    """WorkflowRenderer 的样式。"""

    wave: Style = Style(icon="◇", fg="warning")
    node_start: Style = Style(icon="▸", fg="bold")
    node_done_ok: Style = Style(icon="■", fg="success")
    node_done_fail: Style = Style(icon="■", fg="error")
    router_match: Style = Style(icon="▸", fg="info")
    router_miss: Style = Style(icon="▹", fg="muted")
    skip: Style = Style(icon="○", fg="muted")
    loop: Style = Style(icon="↻", fg="warning")
    fanout: Style = Style(icon="⊞", fg="info")
    map_item: Style = Style(icon="▪", fg="muted")
    header: Style = Style(icon="●", fg="warning")
    separator: Style = Style(fg="warning")


@dataclass(frozen=True)
class SpinnerStyles:
    """Spinner 的样式。"""

    frame: str = "dim"       # 语义颜色名
    elapsed: str = "info"    # 语义颜色名

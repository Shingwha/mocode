"""CLI visual configuration — Theme as composition wrapper."""

from __future__ import annotations

from dataclasses import dataclass, field

from .palette import ColorPalette, DEFAULT_PALETTE
from .styles import DisplayStyles, WorkflowStyles, SpinnerStyles


@dataclass
class Theme:
    """样式配置包 — 组合 palette + 所有组件样式组。

    Theme 不是"样式注册表"，而是"打包方便传递"的组合对象。
    组件不应直接读取 Theme，而应接收自己需要的 *Styles 子集。

    换肤方式::

        # 方式 1：换 palette（所有颜色跟着变）
        Theme(palette=ColorPalette(success="\\033[92m", ...))

        # 方式 2：换个别样式（只改一个组件的一个样式）
        from .style import Style
        Theme(display=DisplayStyles(tool_done=Style(icon="✔", fg="success")))

        # 方式 3：换整个组件样式组
        Theme(workflow=WorkflowStyles(separator=Style(fg="accent")))
    """

    palette: ColorPalette = field(default_factory=lambda: DEFAULT_PALETTE)

    display: DisplayStyles = field(default_factory=DisplayStyles)
    workflow: WorkflowStyles = field(default_factory=WorkflowStyles)
    spinner: SpinnerStyles = field(default_factory=SpinnerStyles)

    # ── 便捷方法：组件用来提取自己需要的部分 ──

    def for_display(self) -> tuple[DisplayStyles, ColorPalette]:
        return self.display, self.palette

    def for_workflow(self) -> tuple[WorkflowStyles, ColorPalette]:
        return self.workflow, self.palette

    def for_spinner(self) -> tuple[SpinnerStyles, ColorPalette]:
        return self.spinner, self.palette


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

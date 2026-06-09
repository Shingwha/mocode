"""Color palette — ANSI escape tokens + semantic color mapping."""

from __future__ import annotations

from dataclasses import dataclass


class C:
    """ANSI escape tokens — 物理层，仅此一处定义。"""

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
    """语义颜色映射 — 换肤只需替换此对象。

    每个字段是一个语义名，值是 ANSI escape string。
    """

    # 状态色
    success: str = C.GREEN
    error: str = C.RED
    warning: str = C.YELLOW
    info: str = C.SOFT_CYAN

    # 层次色
    bold: str = C.BOLD
    dim: str = C.DIM
    muted: str = C.GRAY
    accent: str = C.CYAN
    highlight: str = C.MAGENTA

    # 背景色
    bg_input: str = C.BG_DARK

    # 特殊
    reset: str = C.RST

    def resolve(self, name: str) -> str:
        """语义名 → ANSI 代码。已是 escape 的透传。

        支持空格分隔的组合色名，如 "dim highlight" → dim + magenta。
        """
        if not name:
            return ""
        if name.startswith("\033["):
            return name
        parts = name.split()
        codes = []
        for part in parts:
            val = getattr(self, part, None)
            if val is None:
                raise ValueError(f"Unknown color: {part!r}")
            codes.append(val)
        return "".join(codes)

    def s(self, text: str, *colors: str) -> str:
        """便捷方法：给文本着色。"""
        codes = "".join(self.resolve(c) for c in colors)
        return f"{codes}{text}{self.reset}" if codes else text


DEFAULT_PALETTE = ColorPalette()

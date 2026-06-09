"""Style — atomic style descriptor with palette-aware rendering."""

from __future__ import annotations

from dataclasses import dataclass

from .palette import ColorPalette


@dataclass(frozen=True, slots=True)
class Style:
    """原子样式描述 — 只描述外观，不负责渲染。

    fg/icon_fg/bg 都是语义颜色名（如 "success"、"dim"），
    通过 palette 解析为实际 ANSI 代码。
    """

    icon: str = ""
    fg: str = ""        # 语义颜色名
    icon_fg: str = ""   # icon 颜色，默认跟随 fg
    bg: str = ""        # 背景语义色名

    def render(self, text: str, palette: ColorPalette, *,
               suffix: str = "", elapsed: float = -1,
               error: str = "") -> str:
        """使用给定 palette 渲染一行文本，返回 ANSI 字符串。"""
        parts: list[str] = []
        p = palette

        # Icon
        if self.icon:
            ic = p.resolve(self.icon_fg or self.fg)
            parts.append(f"{ic}{self.icon}{p.reset}")

        # Text
        fg = p.resolve(self.fg)
        bg = p.resolve(self.bg)
        if bg and fg:
            parts.append(f"{bg}{fg}{text}{p.reset}")
        elif fg:
            parts.append(f"{fg}{text}{p.reset}")
        else:
            parts.append(text)

        # Suffix
        if suffix:
            parts.append(f"{p.dim}{suffix}{p.reset}")

        # Error
        if error:
            ec = p.resolve(self.icon_fg or self.fg)
            parts.append(f"{ec}{error}{p.reset}")

        # Elapsed
        if elapsed >= 0.1:
            parts.append(f"{p.dim}{elapsed:.1f}s{p.reset}")

        return " ".join(parts)

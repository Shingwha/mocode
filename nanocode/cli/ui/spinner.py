"""动态加载动画组件"""

import asyncio
import sys
from enum import Enum
from typing import List, Optional

from .colors import CYAN, DIM, GREEN, RESET


class SpinnerStyle(Enum):
    """Spinner 样式"""
    DOTS = "dots"
    LINE = "line"
    ARROW = "arrow"
    PULSE = "pulse"
    MOON = "moon"
    THINKING = "thinking"


# 预定义的 spinner 样式
SPINNER_STYLES = {
    SpinnerStyle.DOTS: ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
    SpinnerStyle.LINE: ["-", "\\", "|", "/"],
    SpinnerStyle.ARROW: ["←", "↖", "↑", "↗", "→", "↘", "↓", "↙"],
    SpinnerStyle.PULSE: ["█", "▉", "▊", "▋", "▌", "▍", "▎", "▏"],
    SpinnerStyle.MOON: ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"],
    SpinnerStyle.THINKING: ["◐", "◓", "◑", "◒"],
}


class Spinner:
    """动态加载动画"""

    def __init__(
        self,
        text: str = "Loading",
        style: SpinnerStyle = SpinnerStyle.DOTS,
        interval: float = 0.08,
    ):
        self.text = text
        self.frames = SPINNER_STYLES.get(style, SPINNER_STYLES[SpinnerStyle.DOTS])
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._current_frame = 0

    async def start(self):
        """开始动画"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._animate())

    async def _animate(self):
        """动画循环"""
        while self._running:
            frame = self.frames[self._current_frame % len(self.frames)]
            # 使用 \r 回到行首，不换行
            sys.stdout.write(f"\r{CYAN}{frame}{RESET} {DIM}{self.text}{RESET}")
            sys.stdout.flush()
            self._current_frame += 1
            await asyncio.sleep(self.interval)

    async def stop(self, clear: bool = True):
        """停止动画"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if clear:
            # 清除行
            sys.stdout.write("\r" + " " * (len(self.text) + 10) + "\r")
            sys.stdout.flush()

    def stop_sync(self, clear: bool = True):
        """同步停止（用于非 async 场景）"""
        self._running = False
        if clear:
            sys.stdout.write("\r" + " " * (len(self.text) + 10) + "\r")
            sys.stdout.flush()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()


class StatusIndicator:
    """状态指示器 - 用于显示任务进度"""

    def __init__(self, text: str):
        self.text = text
        self.spinner = Spinner(text, style=SpinnerStyle.DOTS)

    async def start(self):
        await self.spinner.start()

    async def success(self, message: Optional[str] = None):
        await self.spinner.stop(clear=False)
        msg = message or self.text
        sys.stdout.write(f"\r{GREEN}✓{RESET} {msg}\n")
        sys.stdout.flush()

    async def error(self, message: Optional[str] = None):
        await self.spinner.stop(clear=False)
        msg = message or self.text
        sys.stdout.write(f"\r{RED}✗{RESET} {msg}\n")
        sys.stdout.flush()


class ProgressGroup:
    """多任务进度组"""

    def __init__(self):
        self.indicators: List[StatusIndicator] = []

    def add(self, text: str) -> StatusIndicator:
        """添加新任务"""
        indicator = StatusIndicator(text)
        self.indicators.append(indicator)
        return indicator

    async def start_all(self):
        """启动所有任务"""
        for indicator in self.indicators:
            await indicator.start()

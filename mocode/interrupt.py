"""中断信号 — CancellationToken + Interrupted 异常

替代旧的 InterruptToken (boolean 轮询)：
- asyncio.Event 替代 0.1s 轮询
- Interrupted(BaseException) 替代 sentinel string
- InterruptReason enum 提供元数据
"""

import asyncio
import threading
from enum import Enum, auto


class InterruptReason(Enum):
    USER = auto()
    PERMISSION_DENIED = auto()


class Interrupted(BaseException):
    """BaseException so it bypasses generic `except Exception` in tool/provider code."""

    def __init__(self, reason: InterruptReason = InterruptReason.USER):
        self.reason = reason
        super().__init__(f"Interrupted: {reason.name}")


class CancellationToken:
    def __init__(self):
        self._flag = threading.Event()
        self._reason: InterruptReason | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_event: asyncio.Event | None = None

    def cancel(self, reason: InterruptReason = InterruptReason.USER):
        self._reason = reason
        self._flag.set()
        if self._async_event and self._loop:
            try:
                self._loop.call_soon_threadsafe(self._async_event.set)
            except RuntimeError:
                pass

    def reset(self):
        self._reason = None
        self._flag.clear()
        if self._async_event:
            self._async_event.clear()

    @property
    def is_cancelled(self) -> bool:
        return self._flag.is_set()

    @property
    def reason(self) -> InterruptReason | None:
        return self._reason

    def check(self):
        """在 yield point 调用，被 cancel 时抛出 Interrupted"""
        if self._flag.is_set():
            raise Interrupted(self._reason or InterruptReason.USER)

    async def cancellable(self, coro):
        """包装协程，cancel 时自动取消。零轮询，用 asyncio.wait 实现"""
        self._ensure_event()
        task = asyncio.ensure_future(coro)

        async def _watcher():
            await self._async_event.wait()

        watcher = asyncio.ensure_future(_watcher())
        done, pending = await asyncio.wait(
            {task, watcher}, return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()
            try:
                await p
            except asyncio.CancelledError:
                pass

        if watcher in done:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise Interrupted(self._reason or InterruptReason.USER)

        return task.result()

    def _ensure_event(self):
        if self._async_event is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
            self._async_event = asyncio.Event()
            if self._flag.is_set():
                self._async_event.set()

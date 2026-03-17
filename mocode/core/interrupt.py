"""中断信号 - 用于打断 AI 回复"""

import threading


class InterruptToken:
    """轻量级中断信号

    用于在 CLI (ESC 键)、Gateway (/cancel 命令) 和 SDK (interrupt() API) 中复用。

    Example:
        token = InterruptToken()
        token.interrupt()  # 触发中断
        if token.is_interrupted:
            # 处理中断
            pass
        token.reset()  # 重置状态，准备下次使用
    """

    def __init__(self):
        self._interrupted = False
        self._lock = threading.Lock()

    def interrupt(self):
        """触发中断"""
        with self._lock:
            self._interrupted = True

    def reset(self):
        """重置状态"""
        with self._lock:
            self._interrupted = False

    @property
    def is_interrupted(self) -> bool:
        """检查是否已被中断"""
        with self._lock:
            return self._interrupted

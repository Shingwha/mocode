"""ESC key monitor - listens for ESC key to interrupt operations"""

import threading
import time

from ...core.interrupt import InterruptToken
from ..ui.prompt import check_esc_key


class ESCMonitor:
    """ESC key listener for canceling operations.

    Listens for ESC key presses in a background thread and triggers
    the interrupt token when detected.
    """

    def __init__(self, interrupt_token: InterruptToken):
        self._interrupt_token = interrupt_token
        self._thread: threading.Thread | None = None
        self._stop_flag = False

    def start(self) -> None:
        """Start listening for ESC key in background thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_flag = False

        def monitor_loop():
            while not self._stop_flag:
                if check_esc_key():
                    self._interrupt_token.interrupt()
                time.sleep(0.05)  # 50ms polling interval

        self._thread = threading.Thread(target=monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the ESC listener thread."""
        self._stop_flag = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    @property
    def is_running(self) -> bool:
        """Check if the monitor is currently running."""
        return self._thread is not None and self._thread.is_alive()

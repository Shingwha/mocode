"""Unified keyboard input utilities"""

import sys
from contextlib import contextmanager

# Module-level pause flag for ESC monitoring
_esc_monitor_paused = False


@contextmanager
def esc_paused():
    """Context manager: pause ESC monitoring."""
    global _esc_monitor_paused
    _esc_monitor_paused = True
    try:
        yield
    finally:
        _esc_monitor_paused = False


def check_esc_key() -> bool:
    """Non-blocking check for ESC key.

    Returns:
        True if ESC pressed, False otherwise
    """
    if _esc_monitor_paused:
        return False

    if sys.platform == "win32":
        import msvcrt
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b"\x1b":
                return True
        return False
    else:
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                return True
        return False


def getch(with_arrows: bool = False) -> str:
    """Cross-platform getch with optional arrow key support.

    Args:
        with_arrows: If False (default), returns "ESC" for escape or "" for arrow keys.
                     If True, returns "UP"/"DOWN"/"LEFT"/"RIGHT" for arrow keys.

    Returns:
        Single character, "ESC", arrow key name, or "" for ignored keys.
    """
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()  # Unicode version, supports Chinese natively
        if ch == '\x00' or ch == '\xe0':  # Special key prefix
            ch = msvcrt.getwch()
            if with_arrows:
                return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(ch, "")
            return ""
        if ch == '\x1b':
            return "ESC"
        return ch
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                import select
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    seq = sys.stdin.read(2)
                    if with_arrows:
                        return {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}.get(
                            seq, ""
                        )
                    return ""  # Arrow key, ignore
                return "ESC"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

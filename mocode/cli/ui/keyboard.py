"""Unified keyboard input utilities"""

import sys


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

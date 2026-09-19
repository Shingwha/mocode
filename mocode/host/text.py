"""Text decoding — turning bytes a shell or a file handed us into a string.

The host renders nothing, so width and ellipsizing are not its business; the
terminal's own text helpers live in ``mocode.cli.text``.
"""

from __future__ import annotations


def decode_bytes(data: bytes) -> str:
    """Decode bytes to string, trying the encodings MoCode meets in practice."""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936", "gb2312"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


__all__ = ["decode_bytes"]

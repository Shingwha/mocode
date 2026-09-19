"""Text decoding — turning bytes a shell or a file handed us into a string.

The width/ellipsize helpers that used to live here are the terminal's business
and now live in ``mocode.cli.text``: the host renders nothing, so it has no
opinion about how wide anything is.
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

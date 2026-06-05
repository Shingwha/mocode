"""Shared helpers for tools — encoding, path validation."""

from __future__ import annotations

from pathlib import Path

from ..core.tool import ToolError


def decode_bytes(data: bytes) -> str:
    """Decode bytes to string, trying multiple encodings."""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936", "gb2312"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_text(path: Path) -> str:
    """Read text file with encoding fallback."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk", errors="replace")


def read_bytes(data: bytes) -> str:
    """Decode bytes to string, trying multiple encodings."""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936", "gb2312"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def require_file(p: Path) -> Path:
    """Validate path exists and is not a directory."""
    if not p.exists():
        raise ToolError(f"File not found: {p}", "file_not_found")
    if p.is_dir():
        raise ToolError(f"Path is a directory: {p}", "invalid_path")
    return p


def require_dir(p: Path) -> Path:
    """Validate path exists and is a directory."""
    if not p.is_dir():
        raise ToolError(f"Directory not found: {p}", "path_not_found")
    return p

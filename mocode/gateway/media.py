"""Media type detection for gateway file transfer"""

from pathlib import Path

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"})
VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".wmv"})
VOICE_EXTS = frozenset({".silk", ".amr", ".mp3", ".wav", ".ogg", ".m4a"})


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def ensure_media_dir(channel: str, user_id: str) -> Path:
    """Create and return ~/.mocode/media/<channel>/<user_id>/ directory."""
    from ..paths import MEDIA_DIR
    d = MEDIA_DIR / channel / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d

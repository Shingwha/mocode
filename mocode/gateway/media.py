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


def _ext_category(ext: str) -> str:
    """Map file extension to a category subdirectory name."""
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in VOICE_EXTS:
        return "audio"
    return "file"


def ensure_media_dir(channel: str, user_id: str, ext: str = "") -> Path:
    """Create and return ~/.mocode/media/<channel>/<user_id>/<category>/ directory.

    Files are sorted into subdirectories by category (image, video, audio, file).
    """
    from ..paths import MEDIA_DIR
    d = MEDIA_DIR / channel / user_id
    if ext:
        d = d / _ext_category(ext)
    d.mkdir(parents=True, exist_ok=True)
    return d
